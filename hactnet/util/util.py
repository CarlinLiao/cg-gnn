"""Cell/tissue graph dataset utility functions."""
from os.path import join
from glob import glob
from typing import Tuple, List, Dict, Any, Optional

from torch import LongTensor, IntTensor, load
from torch.cuda import is_available
from torch.utils.data import Dataset
from dgl import batch, DGLGraph
from dgl.data.utils import load_graphs

from hactnet.util.cell_graph_model import CellGraphModel


IS_CUDA = is_available()
DEVICE = 'cuda:0' if IS_CUDA else 'cpu'
COLLATE_FN = {
    'DGLGraph': batch,
    'DGLHeteroGraph': batch,
    'Tensor': lambda x: x,
    'int': lambda x: IntTensor(x).to(DEVICE),
    'int64': lambda x: IntTensor(x).to(DEVICE),
    'float': lambda x: LongTensor(x).to(DEVICE)
}
FEATURES = 'feat'

# model parameters
DEFAULT_GNN_PARAMS = {
    'layer_type': "pna_layer",
    'output_dim': 64,
    'num_layers': 3,
    'readout_op': "lstm",
    'readout_type': "mean",
    'aggregators': "mean max min std",
    'scalers': "identity amplification attenuation",
    'avg_d': 4,
    'dropout': 0.,
    'graph_norm': True,
    'batch_norm': True,
    'towers': 1,
    'pretrans_layers': 1,
    'posttrans_layers': 1,
    'divide_input': False,
    'residual': False
}
DEFAULT_CLASSIFICATION_PARAMS = {
    'num_layers': 2,
    'hidden_dim': 128
}


def load_cell_graphs(graph_path: str) -> Tuple[List[DGLGraph], List[int]]:
    "Load cell graphs."
    cg_fnames = glob(join(graph_path, '*.bin'))
    cg_fnames.sort()
    graph_packets = [load_graphs(join(
        graph_path, fname)) for fname in cg_fnames]
    graphs = [entry[0][0] for entry in graph_packets]
    graph_labels = [entry[1]['label'].item() for entry in graph_packets]
    return graphs, graph_labels


class CGDataset(Dataset):
    """Cell graph dataset."""

    def __init__(
        self,
        cell_graphs: Tuple[List[DGLGraph], List[int]],
        load_in_ram: bool = False
    ):
        """
        Cell graph dataset constructor.

        Args:
            cell_graphs (Tuple[List[DGLGraph], List[int]]):
                Cell graphs for a given split (e.g., test) and their labels.
            load_in_ram (bool, optional): Loading data in RAM. Defaults to False.
        """
        super(CGDataset, self).__init__()

        self.cell_graphs = cell_graphs[0]
        self.cell_graph_labels = cell_graphs[1]
        self.num_cg = len(self.cell_graphs)
        self.load_in_ram = load_in_ram

    def __getitem__(self, index):
        """
        Get an example.
        Args:
            index (int): index of the example.
        """
        cg = self.cell_graphs[index]
        label = self.cell_graph_labels[index]
        if IS_CUDA:
            cg = cg.to('cuda:0')
        return cg, label

    def __len__(self):
        """Return the number of samples in the dataset."""
        return self.num_cg


def instantiate_model(cell_graphs: Tuple[List[DGLGraph], List[int]],
                      gnn_params: Dict[str, Any] = DEFAULT_GNN_PARAMS,
                      classification_params: Dict[str,
                                                  Any] = DEFAULT_CLASSIFICATION_PARAMS,
                      model_checkpoint_path: Optional[str] = None
                      ) -> CellGraphModel:
    "Returns a model set up as specified."
    model = CellGraphModel(
        gnn_params=gnn_params,
        classification_params=classification_params,
        node_dim=cell_graphs[0][0].ndata['feat'].shape[1],
        num_classes=int(max(cell_graphs[1]))+1
    ).to(DEVICE)
    if model_checkpoint_path is not None:
        model.load_state_dict(load(model_checkpoint_path))
    return model


def collate(example_batch):
    """
    Collate a batch.
    Args:
        example_batch (torch.tensor): a batch of examples.
    Returns:
        data: (tuple)
        labels: (torch.LongTensor)
    """
    def collate_fn(batch, id, type):
        return COLLATE_FN[type]([example[id] for example in batch])

    # collate the data
    # should 2 if CG or TG processing or 4 if HACT
    num_modalities = len(example_batch[0])
    example_batch = tuple([collate_fn(example_batch, mod_id, type(example_batch[0][mod_id]).__name__)
                           for mod_id in range(num_modalities)])

    return example_batch
