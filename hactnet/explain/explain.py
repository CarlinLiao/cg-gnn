"""
Explain a cell graph (CG) prediction using a pretrained CG-GNN and a graph explainer.

As used in:
"Quantifying Explainers of Graph Neural Networks in Computational Pathology", Jaume et al, CVPR, 2021.
"""

from typing import List, Optional, Tuple, Dict, DefaultDict

from dgl import DGLGraph
from numpy import ndarray
from pandas import DataFrame

from hactnet.util import CellGraphModel
from hactnet.util.util import GraphData
from .importance import calculate_importance
from .plot_interactives import generate_interactives
from .separability import calculate_separability


def explain_cell_graphs(cell_graphs_data: List[GraphData],
                        model: CellGraphModel,
                        explainer_model: str,
                        attributes: List[ndarray],
                        attribute_names: List[str],
                        prune_misclassified: bool = True,
                        concept_grouping: Optional[Dict[str,
                                                        List[str]]] = None,
                        risk: Optional[ndarray] = None,
                        patho_prior: Optional[ndarray] = None,
                        merge_rois: bool = True,
                        feature_names: Optional[List[str]] = None,
                        cell_graph_names: Optional[List[str]] = None,
                        out_directory: Optional[str] = None
                        ) -> Tuple[DataFrame, DataFrame, Dict[Tuple[int, int], DataFrame]]:
    "Generate explanations for all the cell graphs."

    cell_graphs_and_labels = ([d.g for d in cell_graphs_data], [
                              d.label for d in cell_graphs_data])
    cell_graphs = calculate_importance(
        cell_graphs_and_labels[0], model, explainer_model)
    if (out_directory is not None) and (feature_names is not None) and \
            (cell_graph_names is not None):
        graph_groups: Dict[str, List[DGLGraph]] = DefaultDict(list)
        for g in cell_graphs_data:
            if merge_rois:
                graph_groups[g.specimen].append(g.g)
            else:
                graph_groups[g.name].append(g.g)
        generate_interactives(graph_groups, feature_names,
                              attribute_names, out_directory)
    elif (feature_names is not None) or (cell_graph_names is not None):
        raise ValueError('feature_names, cell_graph_names, and out_directory must all be provided '
                         'to create interactive plots.')
    return calculate_separability(
        cell_graphs_and_labels, model, attributes, attribute_names,
        prune_misclassified=prune_misclassified, concept_grouping=concept_grouping, risk=risk,
        patho_prior=patho_prior, out_directory=out_directory)
