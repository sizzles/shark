'''
Train
Train your nerual network
Author: Tawn Kramer
'''
from __future__ import print_function
import os
import sys
import glob
import time
import fnmatch
import argparse
import numpy as np
from PIL import Image
import keras
import conf
import random
import augment

conf.init()

'''
matplotlib can be a pain to setup. So handle the case where it is absent. When present,
use it to generate a plot of training results.
'''
try:
    import matplotlib
    # Force matplotlib to not use any Xwindows backend.
    matplotlib.use('Agg')

    import matplotlib.pyplot as plt
    do_plot = True
except:
    do_plot = False

import models
from load_data import *

def shuffle(samples):
    '''
    randomly mix a list and return a new list
    '''
    ret_arr = []
    len_samples = len(samples)
    while len_samples > 0:
        iSample = random.randrange(0, len_samples)
        ret_arr.append(samples[iSample])
        del samples[iSample]
        len_samples -= 1
    return ret_arr

def parse_img_filepath(filepath):
    f = filepath.split('/')[-1]
    f = f.split('.')[0]
    f = f.split('_')

    '''
    The neural network seems to train well on values that are not too large or small.
    We recorded the raw axis values. So we normalize them and then apply a STEERING_NN_SCALE
    that puts them roughly in units of degrees +- 30 or so.
    '''
    steering = float(f[3]) / float(conf.js_axis_scale) * conf.STEERING_NN_SCALE
    throttle = float(f[5]) / float(conf.js_axis_scale) * conf.STEERING_NN_SCALE
    
    data = {'steering':steering, 'throttle':throttle }
    return data

def generator(samples, batch_size=32, perc_to_augment=0.5, transposeImages=False):
    '''
    Rather than keep all data in memory, we will make a function that keeps
    it's state and returns just the latest batch required via the yield command.
    
    As we load images, we can optionally augment them in some manner that doesn't
    change their underlying meaning or features. This is a combination of
    brightness, contrast, sharpness, and color PIL image filters applied with random
    settings. Optionally a shadow image may be overlayed with some random rotation and
    opacity.
    We flip each image horizontally and supply it as a another sample with the steering
    negated.
    '''
    num_samples = len(samples)
    do_augment = False
    if do_augment:
        shadows = augment.load_shadow_images('./shadows/*.png')    
    
    batch_size = int(batch_size / 2)
    while 1: # Loop forever so the generator never terminates
        samples = shuffle(samples)
        #divide batch_size in half, because we double each output by flipping image.
        for offset in range(0, num_samples, batch_size):
            batch_samples = samples[offset:offset+batch_size]
            print(".", end="")
            
            images = []
            controls = []
            for fullpath in batch_samples:
                try:
                    data = parse_img_filepath(fullpath)
                
                    steering = data["steering"]
                    throttle = data["throttle"]

                    try:
                        image = Image.open(fullpath)
                    except:
                        image = None

                    if image is None:
                        print('failed to open', fullpath)
                        continue

                    #PIL Image as a numpy array
                    image = np.array(image)

                    if do_augment and random.uniform(0.0, 1.0) < perc_to_augment:
                        image = augment.augment_image(image, shadows)

                    if transposeImages:
                        image = image.transpose()

                    center_angle = steering
                    images.append(image)
                    controls.append([center_angle, throttle])

                    #flip image and steering.
                    image = np.fliplr(image)
                    center_angle = -center_angle
                    images.append(image)
                    controls.append([center_angle, throttle])
                except:
                    yield [], []


            # final np array to submit to training
            X_train = np.array(images)
            y_train = np.array(controls)
            yield X_train, y_train


def get_files(filemask):
    '''
    use a filemask and search a path recursively for matches
    '''
    path, mask = os.path.split(filemask)
    matches = []
    for root, dirnames, filenames in os.walk(path):
        for filename in fnmatch.filter(filenames, mask):
            matches.append(os.path.join(root, filename))
    return matches


def train_test_split(lines, test_perc):
    '''
    split a list into two parts, percentage of test used to seperate
    '''
    train = []
    test = []

    for line in lines:
        if random.uniform(0.0, 1.0) < test_perc:
            test.append(line)
        else:
            train.append(line)

    return train, test

def make_generators(inputs, limit=None, batch_size=32, aug_perc=0.0, transposeImages=False):
    '''
    load the job spec from the csv and create some generator for training
    '''
    
    #get the image/steering pairs from the csv files
    lines = get_files(inputs)
    print("found %d files" % len(lines))

    if limit is not None:
        lines = lines[:limit]
        print("limiting to %d files" % len(lines))
    
    train_samples, validation_samples = train_test_split(lines, test_perc=0.2)

    print("num train/val", len(train_samples), len(validation_samples))
    
    # compile and train the model using the generator function
    train_generator = generator(train_samples, batch_size=batch_size, perc_to_augment=aug_perc, transposeImages=transposeImages)
    validation_generator = generator(validation_samples, batch_size=batch_size, perc_to_augment=0.0, transposeImages=transposeImages)
    
    #double each because we will flip image in generator
    n_train = len(train_samples) * 2
    n_val = len(validation_samples) * 2
    
    return train_generator, validation_generator, n_train, n_val


def go(model_name, epochs=50, inputs='./log/*.jpg', limit=None, aug_mult=1, aug_perc=0.0):

    print('working on model', model_name)

    '''
    modify config.json to select the model to train.
    '''
    if conf.model_selection == "nvidia_transposed_inputs":
        model = models.get_nvidia_model()
    elif conf.model_selection == "nvidia_standard_inputs":
        model = models.get_nvidia_model2()
    elif conf.model_selection == "simple":
        model = models.get_simple_model()
    else:
        model = models.get_nvidia_model()

    transposeImages = (model.ch_order == 'channel_first')
    
    callbacks = [
        keras.callbacks.EarlyStopping(monitor='val_loss', patience=conf.training_patience, verbose=0),
        keras.callbacks.ModelCheckpoint(model_name, monitor='val_loss', save_best_only=True, verbose=0),
    ]
    
    batch_size = conf.training_batch_size


    #Train on session images
    train_generator, validation_generator, n_train, n_val = make_generators(inputs, limit=limit, batch_size=batch_size, aug_perc=aug_perc, transposeImages=transposeImages)

    history = model.fit_generator(train_generator, 
        samples_per_epoch = n_train,
        validation_data = validation_generator,
        nb_val_samples = n_val,
        nb_epoch=epochs,
        verbose=1,
        callbacks=callbacks)
    
    try:
        if do_plot:
            # summarize history for loss
            plt.plot(history.history['loss'])
            plt.plot(history.history['val_loss'])
            plt.title('model loss')
            plt.ylabel('loss')
            plt.xlabel('epoch')
            plt.legend(['train', 'test'], loc='upper left')
            plt.savefig('loss.png')
    except:
        print("problems with loss graph")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='train script')
    parser.add_argument('model', type=str, help='model name')
    parser.add_argument('--epochs', type=int, default=conf.training_default_epochs, help='number of epochs')
    parser.add_argument('--inputs', default='./log/*.jpg', help='input mask to gather images')
    parser.add_argument('--limit', type=int, default=None, help='max number of images to train with')
    parser.add_argument('--aug_mult', type=int, default=conf.training_default_aug_mult, help='how many more images to augment')
    parser.add_argument('--aug_perc', type=float, default=conf.training_default_aug_percent, help='what percentage of images to augment 0 - 1')
    args = parser.parse_args()
    
    go(args.model, epochs=args.epochs, limit=args.limit, inputs=args.inputs, aug_mult=args.aug_mult, aug_perc=args.aug_perc)

#python train.py mymodel_aug_90_x4_e200 --epochs=200 --aug_mult=4 --aug_perc=0.9
