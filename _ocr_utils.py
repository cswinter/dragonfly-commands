#!/usr/bin/env python
# (c) Copyright 2020 by James Stout
# Licensed under the LGPL, see <http://www.gnu.org/licenses/>

"""Actions for understanding and manipulating the screen using OCR."""

import pytesseract
import numpy as np
from PIL import Image, ImageGrab, ImageOps
from skimage import filters, transform


DATA_PATH = r"C:\Program Files\Tesseract-OCR\tessdata"
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def find_nearby_words(screen_position):
    image, bounding_box = screenshot_nearby(screen_position)
    results = find_words_in_image(image)
    # Adjust bounding box offsets based on screenshot offset.
    results["left"] = results["left"] + bounding_box[0]
    results["top"] = results["top"] + bounding_box[1]
    return results


def find_nearest_word_position(word, screen_position, ocr_results):
    lowercase_word = word.lower()
    # TODO Investigate why this is >10X faster than the following:
    # possible_matches = ocr_results[ocr_results.text.str.contains(word, case=False, na=False, regex=False)]
    indices = []
    for index, result in ocr_results.iterrows():
        text = result.text
        text = text if isinstance(text, basestring) else str(text)
        if lowercase_word in text.lower():
            indices.append(index)
    possible_matches = ocr_results.loc[indices]
    possible_matches["center_x"] = possible_matches["left"] + possible_matches["width"] / 2
    possible_matches["center_y"] = possible_matches["top"] + possible_matches["height"] / 2
    possible_matches["distance_squared"] = distance_squared(possible_matches["center_x"],
                                                            possible_matches["center_y"],
                                                            screen_position[0],
                                                            screen_position[1])
    best_match = possible_matches.loc[possible_matches["distance_squared"].idxmin()]
    return (best_match["center_x"], best_match["center_y"])


def distance_squared(x1, y1, x2, y2):
    x_dist = (x1 - x2)
    y_dist = (y1 - y2)
    return x_dist * x_dist + y_dist * y_dist


def screenshot_nearby(screen_position, radius=100):
    image = ImageGrab.grab()
    # TODO Consider cropping within grab() for performance. Requires knowledge
    # of screen bounds.
    bounding_box = (max(0, screen_position[0] - radius),
                    max(0, screen_position[1] - radius),
                    min(image.width, screen_position[0] + radius),
                    min(image.height, screen_position[1] + radius))
    return (image.crop(bounding_box), bounding_box)


def find_words_in_image(image):
    threshold_function = lambda data: filters.threshold_otsu(data)
    correction_block_size = 61
    margin = 40
    resize_factor = 3
    convert_grayscale = True
    shift_channels = True
    label_components = False
    preprocessed_image = preprocess(image,
                                    threshold_function=threshold_function,
                                    correction_block_size=correction_block_size,
                                    margin=margin,
                                    resize_factor=resize_factor,
                                    convert_grayscale=convert_grayscale,
                                    shift_channels=shift_channels,
                                    label_components=label_components,
                                    save_debug_images=False)
    tessdata_dir_config = r'--tessdata-dir "{}"'.format(DATA_PATH)
    results = pytesseract.image_to_data(preprocessed_image,
                                        config=tessdata_dir_config,
                                        output_type=pytesseract.Output.DATAFRAME)
    results[["top", "left", "width", "height"]] = ((results[["top", "left", "width", "height"]]
                                                    - margin)
                                                   / resize_factor)
    return results


def window_sums(image, window_size):
    integral = transform.integral_image(image)
    radius = int((window_size - 1) / 2)
    top_left = np.zeros(image.shape, dtype=np.uint16)
    top_left[radius:, radius:] = integral[:-radius, :-radius]
    top_right = np.zeros(image.shape, dtype=np.uint16)
    top_right[radius:, :-radius] = integral[:-radius, radius:]
    top_right[radius:, -radius:] = integral[:-radius, -1:]
    bottom_left = np.zeros(image.shape, dtype=np.uint16)
    bottom_left[:-radius, radius:] = integral[radius:, :-radius]
    bottom_left[-radius:, radius:] = integral[-1:, :-radius]
    bottom_right = np.zeros(image.shape, dtype=np.uint16)
    bottom_right[:-radius, :-radius] = integral[radius:, radius:]
    bottom_right[-radius:, :-radius] = integral[-1:, radius:]
    bottom_right[:-radius, -radius:] = integral[radius:, -1:]
    bottom_right[-radius:, -radius:] = integral[-1, -1]
    return bottom_right - bottom_left - top_right + top_left


def shift_channel(data, channel_index):
    # Shift each channel based on actual position in a typical LCD. This reduces
    # artifacts from subpixel rendering. Note that this assumes RGB
    # left-to-right ordering and a subpixel size of 1 in the resized image.
    channel_shift = channel_index - 1
    if channel_shift != 0:
        data = np.roll(data, channel_shift, axis=1)
        if channel_shift == -1:
            data[:, -1] = data[:, -2]
        elif channel_shift == 1:
            data[:, 0] = data[:, 1]
    return data


def binarize_channel(data, channel_index, threshold_function, correction_block_size, label_components, save_debug_images):
    if save_debug_images:
        Image.fromarray(data).save("debug_before_{}.png".format(channel_index))
    threshold = threshold_function(data)
    if save_debug_images:
        if threshold.ndim == 2:
            Image.fromarray(threshold.astype(np.uint8)).save("debug_threshold_{}.png".format(channel_index))
        else:
            Image.fromarray(np.ones_like(data) * threshold).save("debug_threshold_{}.png".format(channel_index))
    data = data > threshold
    if label_components:
        labels, num_labels = measure.label(data, background=-1, return_num=True)
        label_colors = np.zeros(num_labels + 1, np.bool_)
        label_colors[labels] = data
        background_labels = filters.rank.modal(labels.astype(np.uint16, copy=False),
                                               morphology.square(correction_block_size))
        background_colors = label_colors[background_labels]
    else:
        white_sums = window_sums(data, correction_block_size)
        black_sums = window_sums(~data, correction_block_size)
        background_colors = white_sums > black_sums
        # background_colors = filters.rank.modal(data.astype(np.uint8, copy=False),
        #                                        morphology.square(correction_block_size))
    if save_debug_images:
        Image.fromarray(background_colors == True).save("debug_background_{}.png".format(channel_index))
    # Make the background consistently white (True).
    data = data == background_colors
    if save_debug_images:
        Image.fromarray(data).save("debug_after_{}.png".format(channel_index))
    return data


def preprocess(image,
               threshold_function,
               correction_block_size,
               margin,
               resize_factor,
               convert_grayscale,
               shift_channels,
               label_components,
               save_debug_images):
    new_size = (image.size[0] * resize_factor, image.size[1] * resize_factor)
    image = image.resize(new_size, Image.NEAREST)
    if save_debug_images:
        image.save("debug_resized.png")

    data = np.array(image)
    if shift_channels:
        channels = [shift_channel(data[:, :, i], i) for i in range(3)]
        data = np.stack(channels, axis=-1)

    if convert_grayscale:
        image = Image.fromarray(data)
        image = image.convert("L")
        data = np.array(image)
        data = binarize_channel(data,
                                None,
                                threshold_function,
                                correction_block_size,
                                label_components,
                                save_debug_images)
        image = Image.fromarray(data)
    else:
        channels = [binarize_channel(data[:, :, i],
                                     i,
                                     threshold_function,
                                     correction_block_size,
                                     label_components,
                                     save_debug_images)
                    for i in range(3)]
        data = np.stack(channels, axis=-1)
        data = np.all(data, axis=-1)
        image = Image.fromarray(data)

    image = ImageOps.expand(image, margin, "white")
    # Ensure consistent performance measurements.
    image.load()
    if save_debug_images:
        image.save("debug_final.png")
    return image