"Explain a cell graph (CG) prediction using a pretrained CG-GNN and a graph explainer."
from argparse import ArgumentParser

from pandas import read_hdf

from hactnet.explain import calculate_separability
from hactnet.util import load_cell_graphs, load_cell_graph_names, instantiate_model


def parse_arguments():
    "Process command line arguments."
    parser = ArgumentParser()
    parser.add_argument(
        '--cg_path',
        type=str,
        help='Path to the cell graphs.',
        required=True
    )
    parser.add_argument(
        '--model_checkpoint_path',
        type=str,
        help='Path to the model checkpoint.',
        required=True
    )
    parser.add_argument(
        '--cell_data_hdf_path',
        type=str,
        help='Where to find the data for cells to lookup feature and phenotype names.',
        required=True
    )
    parser.add_argument(
        '--prune_misclassified',
        help='Remove entries for misclassified cell graphs when calculating separability scores.',
        action='store_true'
    )
    parser.add_argument(
        '--out_directory',
        type=str,
        help='Where to save the output graph visualizations.',
        default=None,
        required=False
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    cell_graphs = load_cell_graphs(args.cg_path)
    df_concept, df_aggregated, dfs_k_dist = calculate_separability(
        cell_graphs,
        instantiate_model(
            cell_graphs, model_checkpoint_path=args.model_checkpoint_path),
        [g.ndata['phenotypes'] for g in cell_graphs[0]],
        [col[3:] for col in read_hdf(
            args.cell_data_hdf_path).columns.values if col.startswith('PH_')],
        prune_misclassified=args.prune_misclassified,
        out_directory=args.out_directory)
    print(df_concept)
    print(df_aggregated)
    for cg_pair, df_k in dfs_k_dist.items():
        print(cg_pair)
        print(df_k)
