# encoding: utf-8
"""
@author:  liaoxingyu
@contact: sherlockliao01@gmail.com
"""

from torch.utils.data import DataLoader

from .collate_batch import train_collate_fn, val_collate_fn
from .datasets import ImageDataset, ImageDatasetInference
from .samplers import RandomIdentitySampler
from .transforms import build_transforms
from .datasets.landmark import Landmark, LandmarkInference


def make_data_loader(cfg):
    train_transforms = build_transforms(cfg, is_train=True)
    val_transforms = build_transforms(cfg, is_train=False)
    num_workers = cfg.DATALOADER.NUM_WORKERS
    dataset = Landmark()

    train_set = ImageDataset(dataset.train, train_transforms)
    if cfg.DATALOADER.SAMPLER == 'softmax':
        train_loader = DataLoader(
            train_set, batch_size=cfg.SOLVER.IMS_PER_BATCH, shuffle=True, num_workers=num_workers,
            collate_fn=train_collate_fn
        )
    else:
        train_loader = DataLoader(
            train_set, batch_size=cfg.SOLVER.IMS_PER_BATCH,
            sampler=RandomIdentitySampler(dataset.train, cfg.SOLVER.IMS_PER_BATCH,
                                          cfg.SOLVER.IMS_PER_EPOCH, cfg.DATALOADER.NUM_INSTANCE),
            num_workers=num_workers, collate_fn=train_collate_fn
        )

    val_set = ImageDataset(dataset.query + dataset.gallery, val_transforms)
    val_loader = DataLoader(
        val_set, batch_size=cfg.TEST.IMS_PER_BATCH, shuffle=False, num_workers=num_workers,
        collate_fn=val_collate_fn
    )
    return train_loader, val_loader, len(dataset.query)


def make_inference_data_loader(cfg):
    infer_transforms = build_transforms(cfg, is_train=False)
    num_workers = cfg.DATALOADER.NUM_WORKERS
    dataset = LandmarkInference(cfg.TEST.INPUT_FILE)

    infer_set = ImageDatasetInference(dataset.infer, infer_transforms)
    infer_loader = DataLoader(
        infer_set, batch_size=cfg.TEST.IMS_PER_BATCH, shuffle=False, num_workers=num_workers,
    )
    return infer_loader
