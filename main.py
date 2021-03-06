from __future__ import print_function

import os
import pickle
import sys
import time
from functools import reduce

import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.utils.data import DataLoader

import preprocessing.bAbIData as bd
from model.QAModel import QAModel
from model.QAModelLSTM import  QAModelLSTM
from utils.utils import create_var, time_since, cuda_model
import pandas as pd



def main(task_i):
    # Some old PY 2.6 hacks to include the dirs
    sys.path.insert(0, 'model/')
    sys.path.insert(0, 'preprocessing/')
    sys.path.insert(0, 'utils/')
    # Can be either 1,2,3 or 6 respective to the evaluated task.
    BABI_TASK = task_i

    print('Training for task: %d' % BABI_TASK)

    base_path = "data/tasks_1-20_v1-2/shuffled" #shuffled

    babi_voc_path = {
        0: "data/tasks_1-20_v1-2/en/test_data",
        1: base_path + "/" + "qa1_single-supporting-fact_train.txt",
        2: base_path + "/" + "qa2_two-supporting-facts_train.txt",
        3: base_path + "/" + "qa3_three-supporting-facts_train.txt",
        6: base_path + "/" + "qa6_yes-no-questions_train.txt"
    }

    babi_train_path = {
        0: "data/tasks_1-20_v1-2/en/test_data",
        1: base_path + "/" + "qa1_single-supporting-fact_train.txt",
        2: base_path + "/" + "qa2_two-supporting-facts_train.txt",
        3: base_path + "/" + "qa3_three-supporting-facts_train.txt",
        6: base_path + "/" + "qa6_yes-no-questions_train.txt"
    }

    babi_test_path = {
        0: "data/tasks_1-20_v1-2/en/test_data",
        1: base_path + "/" + "qa1_single-supporting-fact_test.txt",
        2: base_path + "/" + "qa2_two-supporting-facts_test.txt",
        3: base_path + "/" + "qa3_three-supporting-facts_test.txt",
        6: base_path + "/" + "qa6_yes-no-questions_test.txt"
    }

    PREVIOUSLY_TRAINED_MODEL = None
    ONLY_EVALUATE = False

    ## GridSearch Parameters
    EPOCHS = [40]  # Mostly you only want one epoch param, unless you want equal models with different training times.
    EMBED_HIDDEN_SIZES = [50]
    STORY_HIDDEN_SIZE = [100]
    N_LAYERS = [1]
    BATCH_SIZE = [16]
    LEARNING_RATE = [0.001]  # 0.0001

    ## Output parameters
    # Makes the training halt between every param set until you close the plot windows. Plots are saved either way.
    PLOT_LOSS_INTERACTIVE = False
    PRINT_BATCHWISE_LOSS = False

    grid_search_params = GridSearchParamDict(EMBED_HIDDEN_SIZES, STORY_HIDDEN_SIZE, N_LAYERS, BATCH_SIZE, LEARNING_RATE,
                                             EPOCHS)

    voc, train_instances, test_instances = load_data(babi_voc_path[BABI_TASK], babi_train_path[BABI_TASK],
                                                     babi_test_path[BABI_TASK])

    # Converts the words of the instances from string representation to integer representation using the vocabulary.
    vectorize_data(voc, train_instances, test_instances)

    for i, param_dict in enumerate(grid_search_params):
        print('\nXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\nParam-Set: %d of %d' % (i + 1, len(grid_search_params)))

        embedding_size = param_dict["embedding_size"]
        story_hidden_size = param_dict["story_hidden_size"]
        n_layers = param_dict["layers"]
        learning_rate = param_dict["learning_rate"]
        batch_size = param_dict["batch_size"]
        epochs = param_dict["epochs"]
        voc_len = len(voc)

        ## Print setting
        readable_params = '\nSettings:\nEMBED_HIDDEN_SIZE: %d\nSTORY_HIDDEN_SIZE: %d\nN_LAYERS: %d\nBATCH_SIZE: ' \
                          '%d\nEPOCHS: %d\nVOC_SIZE: %d\nLEARNING_RATE: %f\n' % (
                              embedding_size, story_hidden_size, n_layers, batch_size, epochs, voc_len, learning_rate)

        print(readable_params)

        train_loader, test_loader = prepare_dataloaders(train_instances, test_instances, batch_size)

        ## Initialize Model and Optimizer
        model = QAModel(voc_len, embedding_size, story_hidden_size, voc_len, n_layers)
        model = cuda_model(model)
        # If a path to a state dict of a previously trained model is given, the state will be loaded here.
        if PREVIOUSLY_TRAINED_MODEL is not None:
            model.load_state_dict(torch.load(PREVIOUSLY_TRAINED_MODEL))

        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        criterion = nn.NLLLoss()

        train_loss, test_loss, train_acc, test_acc, eval_lists = conduct_training(model, train_loader, test_loader, optimizer,
                                                                      criterion, only_evaluate=ONLY_EVALUATE,
                                                                      print_loss=PRINT_BATCHWISE_LOSS, epochs=epochs)

        evaluated_out = evaluate_outputs(eval_lists, voc)
        params = [embedding_size, story_hidden_size, n_layers, batch_size, epochs, voc_len, learning_rate, epochs]
        save_results(BABI_TASK, train_loss, test_loss, params, train_acc, test_acc, readable_params, model, voc, evaluated_out)

        # Plot Loss
        if PLOT_LOSS_INTERACTIVE:
            plot_data_in_window(train_loss, test_loss, train_acc, test_acc)


def replace_to_text_vec(ids_vector, voc):
    st_list = []
    for i in range(len(ids_vector)):
        vec = ids_vector[i, :]
        vec = vec.tolist()
        vec = [voc.id_to_word(item)  for item in vec]
        st = ' '.join(vec)
        st_list.append(st)
    return st_list

def evaluate_outputs(eval_lists, voc):
    # Merge Batches
    stories = np.vstack([x[1] for x in eval_lists])
    GT = np.hstack([x[2] for x in eval_lists])
    story_l = np.hstack([x[3] for x in eval_lists])
    query_l = np.hstack([x[4] for x in eval_lists])
    answer = np.hstack([x[5] for x in eval_lists])
    queries = np.vstack([x[6] for x in eval_lists])
    tf = np.equal(GT, answer)

    answer_dist = np.vstack([tf, story_l, query_l])
    answer_dist = np.transpose(answer_dist)
    answer_dist = pd.DataFrame(answer_dist)
    answer_dist.columns = ["Correct", "Story Length", "Query Length"]
    stories_origin = replace_to_text_vec(stories, voc)
    stories_origin = pd.DataFrame(stories_origin)
    queries_origin = replace_to_text_vec(queries, voc)
    queries_origin = pd.DataFrame(queries_origin)
    results = [["Answers Dis", "Original Stories", "Original Queries"], answer_dist, stories_origin, queries_origin]
    return results

def train(model, train_loader, optimizer, criterion, start, epoch, print_loss=False):
    total_loss = 0
    correct = 0
    train_loss_history = []

    train_data_size = len(train_loader.dataset)

    # Set model in training mode
    model.train()

    # The train loader will give us batches of data according to batch size. Example:
    # Batch size is 32 training samples and stories are padded to 66 words (each represented by an integer for the
    # vocabulary index)
    # The stories parameter will contain a tensor of size 32x66. Likewise for the other parameters
    for i, (stories, queries, answers, sl, ql) in enumerate(train_loader, 1):

        stories = create_var(stories.type(torch.LongTensor))
        queries = create_var(queries.type(torch.LongTensor))
        answers = create_var(answers.type(torch.LongTensor))
        sl = create_var(sl.type(torch.LongTensor))
        ql = create_var(ql.type(torch.LongTensor))

        # Sort stories by their length (because of packing in the forward step!)
        sl, perm_idx = sl.sort(0, descending=True)
        stories = stories[perm_idx]
        ql = ql[perm_idx]
        queries = queries[perm_idx]
        answers = answers[perm_idx]

        output = model(stories, queries, sl, ql)

        answers_flat = answers.view(-1)

        loss = criterion(output, answers)

        total_loss += loss.data[0]

        # Calculating elementwise loss per batch
        train_loss_history.append(loss.data[0])

        model.zero_grad()
        loss.backward()
        optimizer.step()

        if print_loss:
            if i % 1 == 0:
                print('[{}] Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.2f}'.format(time_since(start), epoch,
                                                                                    i * len(stories),
                                                                                    len(train_loader.dataset),
                                                                                    100. * i * len(stories) / len(
                                                                                        train_loader.dataset),
                                                                                    loss.data[0]))

        pred_answers = output.data.max(1)[1]
        correct += pred_answers.eq(
            answers.data.view_as(pred_answers)).cpu().sum()  # calculate how many labels are correct

    accuracy = 100. * correct / train_data_size

    print('Training set: Accuracy: {}/{} ({:.0f}%)'.format(correct, train_data_size, accuracy))

    return train_loss_history, accuracy, total_loss  # loss per epoch


def test(model, test_loader, criterion, PRINT_LOSS=False):
    model.eval()

    if PRINT_LOSS:
        print("evaluating trained model ...")

    correct = 0
    test_data_size = len(test_loader.dataset)

    test_loss_history = []
    stats_list = []
    for stories, queries, answers, sl, ql in test_loader:
        stories_np  = stories.numpy()
        answers_np = answers.numpy()
        storyl_np = sl.numpy()
        queryl_np = ql.numpy()
        queries_np = queries.numpy()
        stories = Variable(stories.type(torch.LongTensor))
        queries = Variable(queries.type(torch.LongTensor))
        answers = Variable(answers.type(torch.LongTensor))
        sl = Variable(sl.type(torch.LongTensor))
        ql = Variable(ql.type(torch.LongTensor))

        # Sort stories by their length
        sl, perm_idx = sl.sort(0, descending=True)
        stories = stories[perm_idx]
        # ql, perm_idx = ql.sort(0, descending=True) # if we sort query also --> then they do not fit together!
        ql = ql[perm_idx]
        queries = queries[perm_idx]
        answers = answers[perm_idx]

        output = model(stories, queries, sl, ql)

        loss = criterion(output, answers.view(-1))

        # Calculating elementwise loss  per batch
        test_loss_history.append(loss.data[0])

        pred_answers = output.data.max(1)[1]
        predicted_answers_np = pred_answers.numpy()
        stats = [["stories", "Ground Truth", "story length", "Q lenght", "Predicted Answer", "Queries"], stories_np, answers_np, storyl_np, queryl_np, predicted_answers_np, queries_np]
        stats_list.append(stats)
        correct += pred_answers.eq(
            answers.data.view_as(pred_answers)).cpu().sum()  # calculate how many labels are correct

    accuracy = 100. * correct / test_data_size

    print('Test set: Accuracy: {}/{} ({:.0f}%)'.format(correct, test_data_size, accuracy))

    return test_loss_history, accuracy, stats_list


def prepare_dataloaders(train_instances, test_instances, batch_size, shuffle=True):
    train_dataset = bd.BAbiDataset(train_instances)
    test_dataset = bd.BAbiDataset(test_instances)

    train_loader = DataLoader(dataset=train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(dataset=test_dataset, batch_size=batch_size, shuffle=True)

    return train_loader, test_loader


class GridSearchParamDict():
    def __init__(self, embeddings, story_hidden_sizes, layers, batch_sizes, learning_rates, epochs):
        self.embeddings = embeddings
        self.story_hiddens = story_hidden_sizes
        self.layers = layers
        self.batch_sizes = batch_sizes
        self.learning_rates = learning_rates
        self.epochs = epochs

        self.params = self.generate_param_set()

    def __len__(self):
        return len(self.params)

    def __getitem__(self, key):
        return self.params[key]

    def generate_param_set(self):
        self.params = []

        for b in self.batch_sizes:
            for lr in self.learning_rates:
                for l in self.layers:
                    for s in self.story_hiddens:
                        for em in self.embeddings:
                            for ep in self.epochs:
                                self.params.append({
                                    "embedding_size": em,
                                    "story_hidden_size": s,
                                    "layers": l,
                                    "batch_size": b,
                                    "learning_rate": lr,
                                    "epochs": ep
                                })

        return self.params


def load_data(voc_path, train_path, test_path):
    voc = bd.Vocabulary()
    train_instances = []
    test_instances = []

    voc.extend_with_file(voc_path)
    train_instances = bd.BAbIInstance.instances_from_file(train_path)
    test_instances = bd.BAbIInstance.instances_from_file(test_path)

    voc.sort_ids()

    return voc, train_instances, test_instances


def vectorize_data(voc, train_instances, test_instances):
    # At this point, training instances have been loaded with real word sentences.
    # Using the vocabulary we convert the words into integer representations, so they can converted with an embedding
    # later on.
    for inst in train_instances:
        inst.vectorize(voc)

    for inst in test_instances:
        inst.vectorize(voc)


def conduct_training(model, train_loader, test_loader, optimizer, criterion, only_evaluate=False, print_loss=False,
                     epochs=1):
    train_loss_history = []
    test_loss_history = []

    train_acc_history = []
    test_acc_history = []
    eval_list = []
    ## Start training
    start = time.time()
    if print_loss:
        print("Training for %d epochs..." % epochs)

    for epoch in range(1, epochs + 1):
        print("Epoche: %d" % epoch)
        # Train cycle
        if not only_evaluate:
            train_loss, train_accuracy, total_loss = train(model, train_loader, optimizer, criterion, start, epoch,
                                                           print_loss)

        # Test cycle
        test_loss, test_accuracy, eval_list = test(model, test_loader, criterion, PRINT_LOSS=False)

        # Add Loss to history
        if not only_evaluate:
            train_loss_history = train_loss_history + train_loss
        test_loss_history = test_loss_history + test_loss
        # Add Loss to history
        if not only_evaluate:
            train_acc_history.append(train_accuracy)
        test_acc_history.append(test_accuracy)

    return train_loss_history, test_loss_history, train_acc_history, test_acc_history, eval_list


def plot_data_in_window(train_loss, test_loss, train_acc, test_acc):
    plt.figure()
    plt.plot(train_loss, label='train-loss', color='b')
    plt.plot(test_loss, label='test-loss', color='r')
    plt.xlabel("Batch")
    plt.ylabel("Average Elementwise Loss per Batch")
    plt.legend()
    plt.show()

    plt.figure()
    plt.plot(train_acc, label='train-accuracy', color='b')
    plt.plot(test_acc, label='test-accuracy', color='r')
    plt.xlabel("Epoch")
    plt.ylabel("Correct answers in %")
    plt.legend()
    plt.show()  # Train cycle


def concatenated_params(params):
    params_str = [str(x) for x in params]
    params_str = reduce(lambda x, y: x + '_' + y, params_str)

    return params_str


def save_results(task, train_loss, test_loss, params, train_accuracy, test_accuracy, params_file, model, voc, eval_results,
                 plots=True):
    param_str = concatenated_params(params)

    date = str(time.strftime("%Y:%m:%d:%H:%M:%S"))
    fname = "results/" + date.replace(":", "_") + "_" + param_str + "_task_" + str(task) + "/"
    try:
        os.stat(fname)
    except:
        os.mkdir(fname)
    tr_loss = np.array(train_loss)
    te_loss = np.array(test_loss)
    tr_acc = np.array(train_accuracy)
    te_acc = np.array(test_accuracy)
    tr_loss.tofile(fname + "train_loss.csv", sep=";")
    te_loss.tofile(fname + "test_loss.csv", sep=";")
    tr_acc.tofile(fname + "train_accuracy.csv", sep=";")
    te_acc.tofile(fname + "test_accuracy.csv", sep=";")
    eval_results[1].to_csv(fname + "distribution_answers.csv", sep=";")
    eval_results[2].to_csv(fname + "Stories.csv", sep=";")
    eval_results[3].to_csv(fname + "Queries.csv", sep=";")
    if plots == True:
        plt.figure()
        plt.plot(train_loss, label='train-loss', color='b')
        plt.plot(test_loss, label='test-loss', color='r')
        plt.xlabel("Batch")
        plt.ylabel("Average Elementwise Loss per Batch")
        plt.legend()
        plt.savefig(fname + "loss_history.png")
        plt.figure()
        plt.plot(train_accuracy, label='train-accuracy', color='b')
        plt.plot(test_accuracy, label='test-accuracy', color='r')
        plt.xlabel("Epoch")
        plt.ylabel("Correct answers in %")
        plt.legend()
        plt.savefig(fname + "acc_history.png")
        plt.close("all")
    with open(fname + "params.txt", "w") as text_file:
        text_file.write(params_file)

    torch.save(model.state_dict(), fname + "trained_model.pth")
    pickle.dump(voc.voc_dict, open(fname + "vocabulary.pkl", "wb"))


if __name__ == "__main__":
    for i in(1,2,3,6):
        main(i)
