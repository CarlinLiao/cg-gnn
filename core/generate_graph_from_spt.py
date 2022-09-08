"TODO: docstring"
from os import path, makedirs, listdir, replace
import logging
import argparse
from pathlib import Path
from random import shuffle
from typing import Optional, Tuple, Union, List, Dict

import dgl
import numpy as np
import torch
from dgl.data.utils import load_graphs, save_graphs
from sklearn.neighbors import kneighbors_graph
from pandas import read_csv, DataFrame
from scipy.spatial.distance import pdist, squareform

LABEL = "label"
CENTROID = "centroid"
FEATURES = "feat"


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--spt_csv_feat_filename',
        type=str,
        help='Path to the SPT features CSV.',
        required=True
    )
    parser.add_argument(
        '--spt_csv_label_filename',
        type=str,
        help='Path to the SPT labels CSV.',
        required=True
    )
    parser.add_argument(
        '--save_path',
        type=str,
        help='Path to save the cell graphs.',
        default='/data/',
        required=False
    )
    parser.add_argument(
        '--val_data_prc',
        type=int,
        help='Percentage of data to use as validation and test set, each. '
        'Must be between 0% and 50%.',
        default=15,
        required=False
    )
    parser.add_argument(
        '--roi_side_length',
        type=int,
        help='Side length in pixels of the ROI areas we wish to generate.',
        default=800,
        required=False
    )
    return parser.parse_args()


def create_graphs_from_spt_csv(spt_csv_feat_filename: str,
                               spt_csv_label_filename: str,
                               output_directory: str,
                               image_size: Tuple[int, int] = (800, 800),
                               k: int = 5,
                               thresh: Optional[int] = None
                               ) -> Dict[str, List[str]]:
    "Create graphs from a feature, location, and label CSV created from SPT."

    # Read in the SPT data and convert the labels from categorical to numeric
    df_feat_all_specimens: DataFrame = read_csv(
        spt_csv_feat_filename, index_col=0)
    df_label_all_specimens: DataFrame = read_csv(
        spt_csv_label_filename, index_col=0)

    # Split the data by specimen (slide)
    filenames: Dict[str, List[str]] = {}
    for specimen, df_specimen in df_feat_all_specimens.groupby('specimen'):

        # Initialize data structures
        bboxes: List[Tuple[int, int, int, int, int, int]] = []
        slide_size = df_specimen[['center_x', 'center_y']].max() + 100
        p_tumor = df_specimen['Tumor'].sum()/df_specimen.shape[0]
        df_tumor = df_specimen.loc[df_specimen['Tumor'], :]
        d_square = squareform(pdist(df_tumor[['center_x', 'center_y']]))
        filenames[specimen] = []

        # Create as many ROIs as images will add up to the proportion of
        # the slide's cells are tumors
        n_rois = np.round(
            p_tumor * np.prod(slide_size) / np.prod(image_size))
        while (len(bboxes) < n_rois) and (df_tumor.shape[0] > 0):
            p_dist = np.percentile(d_square, p_tumor, axis=0)
            x, y = df_specimen.iloc[np.argmin(
                p_dist), :][['center_x', 'center_y']].tolist()
            x_min = x - image_size[0]//2
            x_max = x + image_size[0]//2
            y_min = y - image_size[1]//2
            y_max = y + image_size[1]//2
            bboxes.append((x_min, x_max, y_min, y_max, x, y))
            p_tumor -= np.prod(image_size) / np.prod(slide_size)
            cells_to_keep = ~df_tumor['center_x'].between(
                x_min, x_max) & ~df_tumor['center_y'].between(y_min, y_max)
            df_tumor = df_tumor.loc[cells_to_keep, :]
            d_square = d_square[cells_to_keep, :][:, cells_to_keep]

        # Create feature, centroid, and label arrays and then the graph
        df_features = df_specimen.drop(
            ['center_x', 'center_y', 'specimen'], axis=1)
        label: int = df_label_all_specimens.loc[specimen, 'result']
        for i, (x_min, x_max, y_min, y_max, x, y) in enumerate(bboxes):
            df_roi = df_specimen.loc[df_specimen['center_x'].between(
                x_min, x_max) & df_specimen['center_y'].between(y_min, y_max), ]
            centroids = df_roi[['center_x', 'center_y']].values
            features = df_features.loc[df_roi.index, ].astype(int).values
            roi_name = f'melanoma_{specimen}_{i}_{image_size[0]}x{image_size[1]}_x{x}_y{y}'
            create_and_save_graph(output_directory,
                                  centroids, features, label,
                                  output_name=roi_name,
                                  k=k, thresh=thresh)
            df_roi.reset_index()['histological_structure'].to_csv(
                path.join(output_directory, 'histological_structure_ids',
                          f'{roi_name}_hist_structs.csv'))
            filenames[specimen].append(f'{roi_name}.bin')

    return filenames


def create_graph(centroids: np.ndarray,
                 features: torch.Tensor,
                 labels: Optional[np.ndarray] = None,
                 k: int = 5,
                 thresh: Optional[int] = None
                 ) -> dgl.DGLGraph:
    """Generate the graph topology from the provided instance_map using (thresholded) kNN
    Args:
        centroids (np.array): Node centroids
        features (torch.Tensor): Features of each node. Shape (nr_nodes, nr_features)
        labels (np.array): Node levels.
        k (int, optional): Number of neighbors. Defaults to 5.
        thresh (int, optional): Maximum allowed distance between 2 nodes.
                                    Defaults to None (no thresholding).
    Returns:
        dgl.DGLGraph: The constructed graph
    """

    # add nodes
    num_nodes = features.shape[0]
    graph = dgl.DGLGraph()
    graph.add_nodes(num_nodes)
    graph.ndata[CENTROID] = torch.FloatTensor(centroids)

    # add node features
    if not torch.is_tensor(features):
        features = torch.FloatTensor(features)
    graph.ndata[FEATURES] = features

    # add node labels/features
    if labels is not None:
        assert labels.shape[0] == centroids.shape[0], \
            "Number of labels do not match number of nodes"
        graph.ndata[LABEL] = torch.FloatTensor(labels.astype(float))

    # build kNN adjacency
    adj = kneighbors_graph(
        centroids,
        k,
        mode="distance",
        include_self=False,
        metric="euclidean").toarray()

    # filter edges that are too far (ie larger than thresh)
    if thresh is not None:
        adj[adj > thresh] = 0

    edge_list = np.nonzero(adj)
    graph.add_edges(list(edge_list[0]), list(edge_list[1]))

    return graph


def create_and_save_graph(save_path: Union[str, Path],
                          centroids: np.ndarray,
                          features: torch.Tensor,
                          label: int,
                          output_name: str = None,
                          k: int = 5,
                          thresh: Optional[int] = None
                          ) -> None:
    """Process and save graphs to provided directory
    Args:
        save_path (Union[str, Path]): Base path to save results to.
        output_name (str): Name of output file
    """
    output_path = Path(save_path) / f"{output_name}.bin"
    if output_path.exists():
        logging.info(
            f"Output of {output_name} already exists, using it instead of recomputing"
        )
        graphs, _ = load_graphs(str(output_path))
        assert len(graphs) == 1
        graph = graphs[0]
    else:
        graph = create_graph(
            centroids, features, k=k, thresh=thresh)
        save_graphs(str(output_path), [graph],
                    {'label': torch.tensor([label])})
    return graph


if __name__ == "__main__":

    # Handle inputs
    args = parse_arguments()
    if not (path.exists(args.spt_csv_feat_filename) and path.exists(args.spt_csv_label_filename)):
        raise ValueError("SPT CSVs to read from do not exist.")
    if not 0 < args.val_data_prc < 50:
        raise ValueError(
            "Validation/test set percentage must be between 0 and 50.")
    val_prop: float = args.val_data_prc/100
    roi_size: Tuple[int, int] = (args.roi_side_length, args.roi_side_length)
    save_path = path.join(args.save_path)

    # Create save directory if it doesn't exist yet
    makedirs(save_path, exist_ok=True)
    makedirs(path.join(save_path,
                       'histological_structure_ids'), exist_ok=True)

    # Check if work has already been done by checking whether train, val, and test folders have
    # been created and populated
    set_directories: List[str] = []
    for set_type in ('train', 'val', 'test'):
        directory = path.join(save_path, set_type)
        if path.isdir(directory) and (len(listdir(directory)) > 0):
            raise RuntimeError(
                f'{set_type} set directory has already been created. '
                'Assuming work is done and terminating.')
        makedirs(directory, exist_ok=True)
        set_directories.append(directory)

    # Create the graphs
    graph_filenames = create_graphs_from_spt_csv(
        args.spt_csv_feat_filename, args.spt_csv_label_filename, save_path, image_size=roi_size)

    # Randomly allocate graphs to train, val, and test sets
    n_graphs = sum(len(l) for l in graph_filenames.values())
    n_train = max(n_graphs*(1 - 2*val_prop), 1)
    n_val = n_test = max(n_graphs*val_prop, 1)
    n_used: int = 0
    train_files: List[str] = []
    val_files: List[str] = []
    test_files: List[str] = []
    all_specimen_files = list(graph_filenames.values())
    shuffle(all_specimen_files)
    for specimen_files in all_specimen_files:
        if n_used < n_train:
            train_files += specimen_files
        elif n_used < n_train + n_val:
            val_files += specimen_files
        else:
            test_files += specimen_files
        n_used += len(specimen_files)
    assert (len(train_files) > 0), 'Training data allocation percentage too low.'
    assert (len(val_files) > 0) and (len(test_files) > 0), \
        'Val/test data allocation percentage too low.'

    # Move the train, val, and test sets into their own dedicated folders
    sets_data = (train_files, val_files, test_files)
    for i in range(3):
        for filename in sets_data[i]:
            replace(path.join(save_path, filename),
                    path.join(set_directories[i], filename))
