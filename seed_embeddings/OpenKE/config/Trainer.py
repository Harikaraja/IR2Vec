# coding:utf-8
import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.optim as optim
import os
import time
import sys
import datetime
import ctypes
import json
import numpy as np
import copy
from tqdm import tqdm
from ray import train
import tempfile
from ray.train import Checkpoint
import ray
from config.Tester import Tester
from scipy import spatial
from collections import OrderedDict
import analogy


class Trainer(object):
    def __init__(
        self,
        model=None,
        data_loader=None,
        train_times=1000,
        alpha=0.5,
        use_gpu=False,
        opt_method="sgd",
        save_steps=None,
        checkpoint_dir=None,
        index_dir=None,
        out_path=None,
    ):

        self.work_threads = 8
        self.train_times = train_times
        self.index_dir = index_dir
        self.opt_method = opt_method
        self.optimizer = None
        self.lr_decay = 0
        self.weight_decay = 0
        self.alpha = alpha

        self.model = model
        self.data_loader = data_loader
        self.use_gpu = use_gpu
        self.save_steps = save_steps
        self.checkpoint_dir = checkpoint_dir
        # self.out_path = out_path

    def train_one_step(self, data):
        self.optimizer.zero_grad()
        loss = self.model(
            {
                "batch_h": self.to_var(data["batch_h"], self.use_gpu),
                "batch_t": self.to_var(data["batch_t"], self.use_gpu),
                "batch_r": self.to_var(data["batch_r"], self.use_gpu),
                "batch_y": self.to_var(data["batch_y"], self.use_gpu),
                "mode": data["mode"],
            }
        )
        loss.backward()
        self.optimizer.step()
        return loss.item()

    def getEntityDict(self, ent_embeddings, index_dir):
        """
        Reads the entity embeddings and returns an dictionary
        mapping entity names to their corresponding embeddings.
        """
        rep = ent_embeddings

        with open(os.path.join(index_dir, "entity2id.txt")) as fEntity:
            content = fEntity.read()

        entities = content.split("\n")
        entity_dict = {}

        for i in range(1, int(entities[0])):
            entity_name = entities[i].split("\t")[0]
            entity_dict[entity_name.upper()] = rep[i - 1].tolist()

        last_entity_name = entities[int(entities[0])].split("\t")[0]
        entity_dict[last_entity_name.upper()] = rep[int(entities[0]) - 1].tolist()

        return entity_dict

    def run(
        self,
        link_prediction=False,
        test_dataloader=None,
        model=None,
        is_analogy=False,
        ray=True,
        freq=10,
    ):
        if self.use_gpu:
            self.model.cuda()

        if self.optimizer != None:
            pass
        elif self.opt_method == "Adagrad" or self.opt_method == "adagrad":
            self.optimizer = optim.Adagrad(
                self.model.parameters(),
                lr=self.alpha,
                lr_decay=self.lr_decay,
                weight_decay=self.weight_decay,
            )
        elif self.opt_method == "Adadelta" or self.opt_method == "adadelta":
            self.optimizer = optim.Adadelta(
                self.model.parameters(),
                lr=self.alpha,
                weight_decay=self.weight_decay,
            )
        elif self.opt_method == "Adam" or self.opt_method == "adam":
            self.optimizer = optim.Adam(
                self.model.parameters(),
                lr=self.alpha,
                weight_decay=self.weight_decay,
            )
        else:
            self.optimizer = optim.SGD(
                self.model.parameters(),
                lr=self.alpha,
                weight_decay=self.weight_decay,
            )
        print("Finish initializing...")

        training_range = tqdm(range(self.train_times))
        for epoch in training_range:
            res = 0.0
            for data in self.data_loader:
                loss = self.train_one_step(data)
                res += loss
            training_range.set_description("Epoch %d | loss: %f" % (epoch, res))
            checkpoint = None
            if ray and epoch % freq == 0:
                metrics = {"loss": res}
                # Link Prediction
                if link_prediction:
                    # model here is the orginal pytorch model
                    tester = Tester(
                        model=model, data_loader=test_dataloader, use_gpu=False
                    )

                    mrr, mr, hit10, hit3, hit1 = tester.run_link_prediction(
                        type_constrain=False, sample_size=200, sample_per=30
                    )

                    metrics.update(
                        {
                            "mrr": mrr,
                            "mr": mr,
                            "hit10": hit10,
                            "hit3": hit3,
                            "hit1": hit1,
                        }
                    )
                    print("Link Prediction Scores Completed")

                if is_analogy:
                    # self.model => Negative Sampling object
                    # self.mode.model => Transe model

                    ent_embeddings = self.model.model.ent_embeddings.weight.data.numpy()
                    entity_dict = self.getEntityDict(ent_embeddings, self.index_dir)
                    analogy_score = analogy.getAnalogyScoreFromDict(
                        entity_dict, self.index_dir
                    )
                    metrics.update({"AnalogiesScore": analogy_score})
                    print("Analogy Score Completed")

                with tempfile.TemporaryDirectory() as temp_checkpoint_dir:
                    # Save the checkpoint...
                    self.model.save_checkpoint(
                        os.path.join(
                            temp_checkpoint_dir,
                            "checkpoint" + "-" + str(epoch) + ".ckpt",
                        )
                    )
                    checkpoint = Checkpoint.from_directory(temp_checkpoint_dir)

                    train.report(metrics, checkpoint=checkpoint)

            elif (
                self.save_steps
                and self.checkpoint_dir
                and (epoch + 1) % self.save_steps == 0
            ):
                print("Epoch %d has finished, saving..." % (epoch))
                self.model.save_checkpoint(
                    os.path.join(self.checkpoint_dir + "-" + str(epoch) + ".ckpt")
                )

        # print("out_path : ", self.out_path)
        # if self.out_path:
        #     print("Inside out_path")
        #     print(self.out_path)
        #     self.model.save_parameters(self.out_path)

    def set_model(self, model):
        self.model = model

    def to_var(self, x, use_gpu):
        if use_gpu:
            return Variable(torch.from_numpy(x).cuda())
        else:
            return Variable(torch.from_numpy(x))

    def set_use_gpu(self, use_gpu):
        self.use_gpu = use_gpu

    def set_alpha(self, alpha):
        self.alpha = alpha

    def set_lr_decay(self, lr_decay):
        self.lr_decay = lr_decay

    def set_weight_decay(self, weight_decay):
        self.weight_decay = weight_decay

    def set_opt_method(self, opt_method):
        self.opt_method = opt_method

    def set_train_times(self, train_times):
        self.train_times = train_times

    def set_save_steps(self, save_steps, checkpoint_dir=None):
        self.save_steps = save_steps
        if not self.checkpoint_dir:
            self.set_checkpoint_dir(checkpoint_dir)

    def set_checkpoint_dir(self, checkpoint_dir):
        self.checkpoint_dir = checkpoint_dir
