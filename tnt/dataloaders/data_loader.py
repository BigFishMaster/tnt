import numpy as np
import random
from collections import Counter
from torch.utils.data import Dataset, DataLoader
from torch.utils.data import WeightedRandomSampler
from torch.utils.data._utils.collate import default_collate
from tnt.utils.logging import logger, beautify_info
from tnt.utils.collate_fn import *
from tnt.dataloaders.field import Field


class GeneralDataLoader(Dataset):
    def __init__(self, cfg, mode="train"):
        self.num_classes = cfg["num_classes"]
        filename = cfg[mode]
        self.data_list = open(filename, "r", encoding="utf8").readlines()
        self.data_list = np.array(self.data_list, dtype=np.string_)
        logger.info("In mode {}, data_list has length of {}.".format(mode, len(self.data_list)))

        if mode == "train" and cfg["use_negative"] is not None:
            negative_info = cfg["use_negative"].split(":")
            if len(negative_info) == 1:
                self.use_negative = negative_info[0]
                self.use_negative_ratio = 0.2
            elif len(negative_info) == 2:
                self.use_negative = negative_info[0]
                self.use_negative_ratio = float(negative_info[1])
            self.negative_data_list = open(self.use_negative, "r", encoding="utf8").readlines()
            self.negative_data_list = np.array(self.negative_data_list, dtype=np.string_)
            logger.info("In mode {}, negative_data_list has length of {} with ratio {}".format(
                mode, len(self.negative_data_list), self.use_negative_ratio))
        else:
            self.use_negative = None

        # field processor
        self._field = Field.from_cfg(cfg, mode=mode)

        # sampling strategy
        self.samples_per_class = None
        sampler_config = cfg["sampler"]
        self.sampler = self._create_sampler(sampler_config, mode)
        logger.info("In mode {}, samples_per_class is {}.".format(mode, self.samples_per_class))
        if mode == "train":
            cfg["samples_per_class"] = self.samples_per_class

        self.collate_fn = self._create_collate(sampler_config)
        self.batch_size = sampler_config.get("batch_size", 10)
        self.num_workers = sampler_config.get("num_workers", 4)

    @classmethod
    def from_config(cls, cfg, mode="train"):
        if (mode not in cfg) or (not cfg[mode]):
            return None
        self = cls(cfg, mode)
        logger.info("===mode-{}===\ncollate_fn: {}\nsampler:{}".format(
            mode, beautify_info(self.collate_fn), beautify_info(self.sampler)))
        is_shuffle = True
        if mode != "train" or self.sampler is not None:
            is_shuffle = False
        pin_memory = cfg["pin_memory"]
        data_loader = DataLoader(self, batch_size=self.batch_size, shuffle=is_shuffle,
                                 num_workers=self.num_workers, collate_fn=self.collate_fn,
                                 pin_memory=pin_memory, sampler=self.sampler, drop_last=(mode != "test"))
        logger.info("data loader is: {}".format(beautify_info(data_loader)))
        return data_loader

    def __getitem__(self, index):
        data = self.data_list[index].decode()
        if self.use_negative is not None and random.random() < self.use_negative_ratio:
            index = random.randint(0, len(self.negative_data_list)-1)
            data = self.negative_data_list[index].decode()
        # there are two cases:
        # train or valid:
        # 1. normal:       image, label
        # 2. multi-label:  image, [label1, label2]
        # 3. weight-label: image, score, label
        # test:
        # 1. image
        result = self._field(data)
        return result

    def __len__(self):
        return len(self.data_list)

    def _create_collate(self, cfg):
        strategy = cfg.get("strategy")
        if strategy == "multilabel_balanced":
            return multilabel_collate_fn
        elif strategy == "pseudo_balanced":
            return pseudolabel_collate_fn
        else:
            return default_collate

    def _create_sampler(self, cfg, mode):
        if mode != "train":
            return None
        strategy = cfg.get("strategy")
        logger.info("In mode {}, sampling strategy is {}".format(mode, strategy))
        num_samples = cfg.get("num_samples") or len(self.data_list)
        replacement = cfg.get("replacement") or False
        use_first_label = cfg.get("use_first_label") or False
        sample_labels = []
        if strategy in ["class_balanced", "pseudo_balanced"]:
            for i, data in enumerate(self.data_list):
                if i % 10000 == 0:
                    logger.info("creating sampler for data: %d/%d", i, len(self.data_list))
                ## support: path label
                ##label = self._field(data.decode(), last=True)
                ## also support: path l1:s1,l2:s2
                label = int(data.decode().strip().split()[-1].split(":")[0])
                sample_labels.append(label)
            logger.info("creating sampler totally: %d", len(self.data_list))

            class_weights = Counter()
            class_weights.update(sample_labels)
            if len(class_weights) != self.num_classes:
                logger.warning("{} classes have no samples.".format(self.num_classes-len(class_weights)))
                for cls in range(self.num_classes):
                    if cls not in class_weights:
                        class_weights.update({cls:1e9})

            sorted_weights = sorted(class_weights.items(), key=lambda x: x[0])
            class_weights = [s[1] for s in sorted_weights]
            self.samples_per_class = class_weights
            weights = 1.0 / np.array(class_weights)
            sample_weights = weights[sample_labels]
            sampler = WeightedRandomSampler(weights=sample_weights, num_samples=num_samples,
                                            replacement=replacement)
            del weights
            del sample_weights
            del sample_labels
            del class_weights
            del sorted_weights
            return sampler
        elif strategy == "instance_weighted":
            sample_weights = np.zeros(len(self.data_list))
            for i, data in enumerate(self.data_list):
                if i % 10000 == 0:
                    logger.info("creating sampler for data: %d/%d", i, len(self.data_list))
                # data is [image, score, label]
                score, label = self._field(data.decode(), last=[1, 2])
                sample_weights[i] = score
                sample_labels.append(label)
            logger.info("creating sampler totally: %d", len(self.data_list))

            class_weights = Counter()
            class_weights.update(sample_labels)
            if len(class_weights) != self.num_classes:
                logger.warning("{} classes have no samples.".format(self.num_classes-len(class_weights)))
                for cls in range(self.num_classes):
                    if cls not in class_weights:
                        class_weights.update({cls:1e9})
            sorted_weights = sorted(class_weights.items(), key=lambda x: x[0])
            class_weights = [s[1] for s in sorted_weights]
            self.samples_per_class = class_weights
            sampler = WeightedRandomSampler(weights=sample_weights, num_samples=num_samples,
                                            replacement=replacement)
            del sample_weights
            del sample_labels
            del class_weights
            del sorted_weights
            return sampler
        elif strategy == "multilabel_balanced":
            class_weights = Counter()
            for i, data in enumerate(self.data_list):
                if i % 100000 == 0:
                    logger.info("creating sampler for data: %d/%d", i, len(self.data_list))
                label = self._field(data.decode(), last=True)
                # label[0] is a list
                if use_first_label:
                    sample_labels.append([label[0][0]])
                    class_weights.update([label[0][0]])
                else:
                    sample_labels.append(label[0])
                    class_weights.update(label[0])
            logger.info("creating sampler totally: %d", len(self.data_list))

            if len(class_weights) != self.num_classes:
                logger.warning("{} classes have no samples.".format(self.num_classes-len(class_weights)))
                for cls in range(self.num_classes):
                    if cls not in class_weights:
                        class_weights.update({cls:1e9})

            sorted_weights = sorted(class_weights.items(), key=lambda x: x[0])
            class_weights = [s[1] for s in sorted_weights]
            self.samples_per_class = class_weights
            weights = 1.0 / np.array(class_weights)
            sample_weights = []
            for i, sample_label in enumerate(sample_labels):
                if i % 100000 == 0:
                    logger.info("prcessoing sample weight: %d/%d", i, len(sample_labels))
                ws = weights[sample_label]
                w = float(np.mean(ws))
                sample_weights.append(w)
            sampler = WeightedRandomSampler(weights=sample_weights, num_samples=num_samples,
                                            replacement=replacement)
            del weights
            del class_weights
            del sorted_weights
            del sample_weights
            del sample_labels
            return sampler
        else:
            return None
