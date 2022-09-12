#!/bin/bash

conda activate hactnet_hpc

python /nadeem_lab/Carlin/hact-net/core/train.py \
--cg_path /nadeem_lab/Carlin/hact-net/data/hact-net-data-v5/tissue_graphs/ \
--model_path /nadeem_lab/Carlin/hact-net/models/ \
--config_fpath /nadeem_lab/Carlin/hact-net/core/config/bracs_tggnn_7_classes_pna.yml \
-b 8 --in_ram --epochs 100 -l 0.0005