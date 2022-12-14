#!/usr/bin/env python3
"""
Train CG-GNN models
"""
from os import makedirs, listdir
from os.path import exists, join
from shutil import rmtree
from typing import Callable, List, Tuple, Optional, Any, Sequence, Dict

from numpy import ndarray, array
from torch import save, load, no_grad, argmax, cat
from torch.cuda import is_available
from torch.optim import Adam, Optimizer
from torch.nn import CrossEntropyLoss
from torch.nn.functional import softmax
from torch.utils.data import ConcatDataset, DataLoader, SubsetRandomSampler
from sklearn.model_selection import KFold
from sklearn.metrics import accuracy_score, f1_score, classification_report
from dgl import DGLGraph
from tqdm import tqdm

from cggnn.util import CellGraphModel, CGDataset, collate, instantiate_model
from cggnn.util.constants import DEFAULT_GNN_PARAMETERS, DEFAULT_CLASSIFICATION_PARAMETERS

# cuda support
IS_CUDA = is_available()
DEVICE = 'cuda:0' if IS_CUDA else 'cpu'


def _set_save_path(model_path: str) -> str:
    "Generate model path if we need to duplicate it and set path to save checkpoints."
    if exists(model_path):
        increment = 2
        while exists(model_path + f'_{increment}'):
            increment += 1
        model_path += f'_{increment}'
    makedirs(model_path, exist_ok=False)
    return model_path


def _create_dataset(cell_graphs: List[DGLGraph],
                    cell_graph_labels: Optional[List[int]] = None,
                    in_ram: bool = True
                    ) -> Optional[CGDataset]:
    "Make a cell graph dataset."
    return CGDataset(cell_graphs, cell_graph_labels, load_in_ram=in_ram) \
        if (len(cell_graphs) > 0) else None


def _create_datasets(
    cell_graph_sets: Tuple[Tuple[List[DGLGraph], List[int]],
                           Tuple[List[DGLGraph], List[int]],
                           Tuple[List[DGLGraph], List[int]]],
    in_ram: bool = True,
    k_folds: int = 3
) -> Tuple[CGDataset, Optional[CGDataset], Optional[CGDataset], Optional[KFold]]:
    "Make the cell and/or tissue graph datasets and the k-fold if necessary."

    train_dataset = _create_dataset(
        cell_graph_sets[0][0], cell_graph_sets[0][1], in_ram)
    assert train_dataset is not None
    validation_dataset = _create_dataset(
        cell_graph_sets[1][0], cell_graph_sets[1][1], in_ram)
    test_dataset = _create_dataset(
        cell_graph_sets[2][0], cell_graph_sets[2][1], in_ram)

    if (k_folds > 0) and (validation_dataset is not None):
        # stack train and validation datasets if both exist and k-fold cross validation is on
        train_dataset = ConcatDataset((train_dataset, validation_dataset))
        validation_dataset = None
    elif (k_folds == 0) and (validation_dataset is None):
        # set k_folds to 3 if not provided and no validation data is provided
        k_folds = 3
    kfold = KFold(n_splits=k_folds, shuffle=True) if k_folds > 0 else None

    return train_dataset, validation_dataset, test_dataset, kfold


def _create_training_dataloaders(train_ids: Optional[Sequence[int]],
                                 test_ids: Optional[Sequence[int]],
                                 train_dataset: CGDataset,
                                 validation_dataset: Optional[CGDataset],
                                 batch_size: int
                                 ) -> Tuple[DataLoader, DataLoader]:
    "Determine whether to k-fold and then create dataloaders."
    if (train_ids is None) or (test_ids is None):
        if validation_dataset is None:
            raise ValueError("validation_dataset must exist.")
        train_dataloader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            shuffle=True,
            collate_fn=collate
        )
        validation_dataloader = DataLoader(
            validation_dataset,
            batch_size=batch_size,
            shuffle=True,
            collate_fn=collate
        )
    else:
        if validation_dataset is not None:
            raise ValueError(
                "validation_dataset provided but k-folding of training dataset requested.")
        train_subsampler = SubsetRandomSampler(train_ids)
        test_subsampler = SubsetRandomSampler(test_ids)
        train_dataloader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            sampler=train_subsampler,
            collate_fn=collate
        )
        validation_dataloader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            sampler=test_subsampler,
            collate_fn=collate
        )

    return train_dataloader, validation_dataloader


def _train_step(model: CellGraphModel,
                train_dataloader: DataLoader,
                loss_fn: Callable,
                optimizer: Optimizer,
                epoch: int,
                fold: int,
                step: int
                ) -> Tuple[CellGraphModel, int]:
    "Train for 1 epoch/fold."

    model.train()
    for batch in tqdm(train_dataloader, desc=f'Epoch training {epoch}, fold {fold}', unit='batch'):

        # 1. forward pass
        labels = batch[-1]
        data = batch[:-1]
        logits = model(*data)

        # 2. backward pass
        loss = loss_fn(logits, labels)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # 4. increment step
        step += 1

    return model, step


def _validation_step(model: CellGraphModel,
                     validation_dataloader: DataLoader,
                     loss_fn: Callable,
                     model_path: str,
                     epoch: int,
                     fold: int,
                     step: int,
                     best_validation_loss: float,
                     best_validation_accuracy: float,
                     best_validation_weighted_f1_score: float
                     ) -> CellGraphModel:
    "Run validation step."

    model.eval()
    all_validation_logits = []
    all_validation_labels = []
    for batch in tqdm(validation_dataloader, desc=f'Epoch validation {epoch}, fold {fold}',
                      unit='batch'):
        labels = batch[-1]
        data = batch[:-1]
        with no_grad():
            logits = model(*data)
        all_validation_logits.append(logits)
        all_validation_labels.append(labels)

    all_validation_logits = cat(all_validation_logits).cpu()
    all_validation_predictions = argmax(all_validation_logits, dim=1)
    all_validation_labels = cat(all_validation_labels).cpu()

    # compute & store loss + model
    with no_grad():
        loss = loss_fn(all_validation_logits, all_validation_labels).item()
    if loss < best_validation_loss:
        best_validation_loss = loss
        save(model.state_dict(), join(
            model_path, 'model_best_validation_loss.pt'))

    # compute & store accuracy + model
    all_validation_predictions = all_validation_predictions.detach().numpy()
    all_validation_labels = all_validation_labels.detach().numpy()
    accuracy = accuracy_score(all_validation_labels,
                              all_validation_predictions)
    if accuracy > best_validation_accuracy:
        best_validation_accuracy = accuracy
        save(model.state_dict(), join(
            model_path, 'model_best_validation_accuracy.pt'))

    # compute & store weighted f1-score + model
    weighted_f1_score = f1_score(
        all_validation_labels, all_validation_predictions, average='weighted')
    if weighted_f1_score > best_validation_weighted_f1_score:
        best_validation_weighted_f1_score = weighted_f1_score
        save(model.state_dict(), join(
            model_path, 'model_best_validation_weighted_f1_score.pt'))

    print(f'Validation loss {loss}')
    print(f'Validation weighted F1 score {weighted_f1_score}')
    print(f'Validation accuracy {accuracy}')

    return model


def _test_model(model: CellGraphModel,
                test_dataset: CGDataset,
                batch_size: int,
                loss_fn: Callable,
                model_path: str,
                step: int
                ) -> CellGraphModel:
    model.eval()
    test_dataloader = DataLoader(
        test_dataset,
        batch_size=batch_size,
        shuffle=True,
        collate_fn=collate
    )

    max_acc = -1.
    max_acc_model_checkpoint = {}

    for metric in ['best_validation_loss', 'best_validation_accuracy',
                   'best_validation_weighted_f1_score']:

        print(f'\n*** Start testing w/ {metric} model ***')

        model_name = [f for f in listdir(
            model_path) if f.endswith(".pt") and metric in f][0]
        checkpoint = load(join(model_path, model_name))
        model.load_state_dict(checkpoint)

        all_test_logits = []
        all_test_labels = []
        for batch in tqdm(test_dataloader, desc=f'Testing: {metric}', unit='batch'):
            labels = batch[-1]
            data = batch[:-1]
            with no_grad():
                logits = model(*data)
            all_test_logits.append(logits)
            all_test_labels.append(labels)

        all_test_logits = cat(all_test_logits).cpu()
        all_test_preds = argmax(all_test_logits, dim=1)
        all_test_labels = cat(all_test_labels).cpu()

        # compute & store loss
        with no_grad():
            loss = loss_fn(all_test_logits, all_test_labels).item()

        # compute & store accuracy
        all_test_preds = all_test_preds.detach().numpy()
        all_test_labels = all_test_labels.detach().numpy()
        accuracy = accuracy_score(all_test_labels, all_test_preds)
        if accuracy > max_acc:
            max_acc = accuracy
            max_acc_model_checkpoint = checkpoint

        # compute & store weighted f1-score
        weighted_f1_score = f1_score(
            all_test_labels, all_test_preds, average='weighted')

        # compute and store classification report
        report = classification_report(
            all_test_labels, all_test_preds, digits=4)
        out_path = join(model_path, 'classification_report.txt')
        with open(out_path, "w", encoding='utf-8') as f:
            f.write(report)

        print(f'Test loss {loss}')
        print(f'Test weighted F1 score {weighted_f1_score}')
        print(f'Test accuracy {accuracy}')

    model.load_state_dict(max_acc_model_checkpoint)
    return model


def train(cell_graph_sets: Tuple[Tuple[List[DGLGraph], List[int]],
                                 Tuple[List[DGLGraph], List[int]],
                                 Tuple[List[DGLGraph], List[int]]],
          save_path: str,
          in_ram: bool = True,
          epochs: int = 10,
          learning_rate: float = 10e-3,
          batch_size: int = 1,
          k_folds: int = 0,
          gnn_parameters: Dict[str, Any] = DEFAULT_GNN_PARAMETERS,
          classification_parameters: Dict[str,
                                          Any] = DEFAULT_CLASSIFICATION_PARAMETERS
          ) -> CellGraphModel:
    "Train CG-GNN."

    # set path to save checkpoints
    save_path = _set_save_path(save_path)

    # make datasets (train, validation & test)
    train_dataset, validation_dataset, test_dataset, kfold = _create_datasets(
        cell_graph_sets, in_ram, k_folds)

    # declare model
    model = instantiate_model(cell_graph_sets[0],
                              gnn_parameters=gnn_parameters,
                              classification_parameters=classification_parameters)

    # build optimizer
    optimizer = Adam(model.parameters(),
                     lr=learning_rate,
                     weight_decay=5e-4)

    # define loss function
    loss_fn = CrossEntropyLoss()

    # training loop
    step: int = 0
    best_validation_loss: float = 10e5
    best_validation_accuracy: float = 0.
    best_validation_weighted_f1_score: float = 0.
    for epoch in range(epochs):

        folds: List[Tuple[Optional[Any], Optional[Any]]] = list(
            kfold.split(train_dataset)) if (kfold is not None) else [(None, None)]

        for fold, (train_ids, test_ids) in enumerate(folds):

            # Determine whether to k-fold and if so how
            train_dataloader, validation_dataloader = _create_training_dataloaders(
                train_ids, test_ids, train_dataset, validation_dataset, batch_size)

            # A.) train for 1 epoch
            model = model.to(DEVICE)
            model, step = _train_step(
                model, train_dataloader, loss_fn, optimizer, epoch, fold, step)

            # B.) validate
            model = _validation_step(model, validation_dataloader, loss_fn, save_path, epoch, fold,
                                     step, best_validation_loss, best_validation_accuracy,
                                     best_validation_weighted_f1_score)

    # testing loop
    if test_dataset is not None:
        model = _test_model(model, test_dataset, batch_size,
                            loss_fn, save_path, step)

    return model


def infer_with_model(model: CellGraphModel,
                     cell_graphs: List[DGLGraph],
                     in_ram: bool = True,
                     batch_size: int = 1,
                     return_probability: bool = False
                     ) -> ndarray:
    "Given a model, infer their classes."

    model = model.eval()

    # make test data loader
    dataset = _create_dataset(cell_graphs, None, in_ram)
    assert dataset is not None
    dataloader = DataLoader(dataset, batch_size=batch_size, collate_fn=collate)

    # start testing
    all_test_logits = []
    for data in tqdm(dataloader, desc='Testing', unit='batch'):
        with no_grad():
            logits = model(*data)
        all_test_logits.append(logits)

    coalesce_function = softmax if return_probability else argmax
    return coalesce_function(cat(all_test_logits).cpu(),
                             dim=1).detach().numpy()


def infer(cell_graphs: Tuple[List[DGLGraph], List[int]],
          model_checkpoint_path: str,
          in_ram: bool = True,
          batch_size: int = 1,
          gnn_params: Dict[str, Any] = DEFAULT_GNN_PARAMETERS,
          classification_params: Dict[str,
                                      Any] = DEFAULT_CLASSIFICATION_PARAMETERS
          ) -> None:
    """
    Test CG-GNN.
    Args:
        args (Namespace): parsed arguments.
    """

    # declare model and load weights
    model = instantiate_model(cell_graphs,
                              gnn_parameters=gnn_params,
                              classification_parameters=classification_params,
                              model_checkpoint_path=model_checkpoint_path)

    # print # of parameters
    pytorch_total_params = sum(p.numel()
                               for p in model.parameters() if p.requires_grad)
    print(pytorch_total_params)

    all_test_preds = infer_with_model(
        model, cell_graphs[0], in_ram, batch_size)
    all_test_labels = array(cell_graphs[1])

    accuracy = accuracy_score(all_test_labels, all_test_preds)
    weighted_f1_score = f1_score(
        all_test_labels, all_test_preds, average='weighted')
    report = classification_report(all_test_labels, all_test_preds)

    print(f'Test weighted F1 score {weighted_f1_score}')
    print(f'Test accuracy {accuracy}')
    print(f'Test classification report {report}')
