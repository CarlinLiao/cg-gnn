import torch
import torch.nn as nn
import importlib

from histocartography.ml.layers.constants import AVAILABLE_LAYER_TYPES, GNN_MODULE


class MultiLayerGNN(nn.Module):
    """
    MultiLayer network that concatenate several gnn layers layer
    """

    def __init__(self, config):
        """
        MultiLayer GNN constructor.
        :param config: (dict) configuration parameters. Refer to the layers implementation
                              for the parameter description.
        """
        super(MultiLayerGNN, self).__init__()

        layer_type = config['layer_type']
        if layer_type in list(AVAILABLE_LAYER_TYPES.keys()):
            module = importlib.import_module(
                GNN_MODULE.format(layer_type)
            )
        else:
            raise ValueError(
                'GNN type: {} not recognized. Options are: {}'.format(
                    layer_type, list(AVAILABLE_LAYER_TYPES.keys())
                )
            )

        self.config = config

        in_dim = config['input_dim']
        hidden_dim = config['hidden_dim']
        out_dim = config['output_dim']
        num_layers = config['n_layers']
        activation = config['activation']
        use_bn = config['use_bn']

        self.layers = nn.ModuleList()

        # input layer
        self.layers.append(getattr(module, AVAILABLE_LAYER_TYPES[layer_type])(
            node_dim=in_dim,
            hidden_dim=hidden_dim,
            out_dim=hidden_dim,
            act=activation,
            layer_id=0,
            use_bn=use_bn,
            config=config)
        )
        # hidden layers
        for i in range(1, num_layers-1):
            self.layers.append(getattr(module, AVAILABLE_LAYER_TYPES[layer_type])(
                node_dim=hidden_dim,
                hidden_dim=hidden_dim,
                out_dim=hidden_dim,
                act=activation,
                layer_id=i,
                use_bn=use_bn,
                config=config)
            )
        # output layer
        self.layers.append(getattr(module, AVAILABLE_LAYER_TYPES[layer_type])(
            node_dim=hidden_dim,
            hidden_dim=hidden_dim,
            out_dim=out_dim,
            act=activation,
            layer_id=num_layers-1,
            use_bn=use_bn,
            config=config)
        )

    def forward(self, g, h, cat=False):
        """
        Forward pass.
        :param g: (DGLGraph)
        :param h: (FloatTensor)
        :param cat: (bool) if concat the features at each conv layer
        :return:
        """
        h_concat = [h]
        for layer in self.layers:
            h = layer(g, h)
            h_concat.append(h)
        if cat:
            return torch.cat(h_concat, dim=-1)
        return h
