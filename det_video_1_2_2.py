import gc
import os
import time

import aicube
import image
import nncase_runtime as nn
import ujson
import ulab.numpy as np
from libs.PipeLine import ScopedTiming
from libs.Utils import *
from media.display import *
from media.media import *
from media.sensor import *

display_mode = "lcd"
if display_mode == "lcd":
    DISPLAY_WIDTH = ALIGN_UP(800, 16)
    DISPLAY_HEIGHT = 480
else:
    DISPLAY_WIDTH = ALIGN_UP(1920, 16)
    DISPLAY_HEIGHT = 1080

OUT_RGB888P_WIDTH = ALIGN_UP(640, 16)
OUT_RGB888P_HEIGH = 360

# 移动平均滤波参数
WINDOW_SIZE = 5  # 窗口大小，保存最近5帧的数据
SMOOTH_THRESHOLD = 0.5  # 新值与平均值的最大差异比例，超过则认为是跳变

root_path = "/sdcard/mp_deployment_source/"
config_path = root_path + "deploy_config.json"
deploy_conf = {}
debug_mode = 1

# 用于存储历史坐标数据，格式: {box_id: [(x, y, w, h), ...]}
history_boxes = {}


def two_side_pad_param(input_size, output_size):
    ratio_w = output_size[0] / input_size[0]  # 宽度缩放比例
    ratio_h = output_size[1] / input_size[1]  # 高度缩放比例
    ratio = min(ratio_w, ratio_h)  # 取较小的缩放比例
    new_w = int(ratio * input_size[0])  # 新宽度
    new_h = int(ratio * input_size[1])  # 新高度
    dw = (output_size[0] - new_w) / 2  # 宽度差
    dh = (output_size[1] - new_h) / 2  # 高度差
    top = int(round(dh - 0.1))
    bottom = int(round(dh + 0.1))
    left = int(round(dw - 0.1))
    right = int(round(dw - 0.1))
    return top, bottom, left, right, ratio


def read_deploy_config(config_path):
    # 打开JSON文件以进行读取deploy_config
    with open(config_path, "r") as json_file:
        try:
            # 从文件中加载JSON数据
            config = ujson.load(json_file)
        except ValueError as e:
            print("JSON 解析错误:", e)
    return config


def moving_average_filter(history, new_value):
    """
    移动平均滤波处理
    history: 历史数据列表
    new_value: 新的坐标值 (x, y, w, h)
    return: 平滑后的坐标值
    """
    # 复制历史数据并添加新值
    new_history = history.copy()
    new_history.append(new_value)

    # 保持窗口大小
    if len(new_history) > WINDOW_SIZE:
        new_history = new_history[-WINDOW_SIZE:]

    # 计算平均值
    x_avg = sum(point[0] for point in new_history) // len(new_history)
    y_avg = sum(point[1] for point in new_history) // len(new_history)
    w_avg = sum(point[2] for point in new_history) // len(new_history)
    h_avg = sum(point[3] for point in new_history) // len(new_history)

    # 检查是否有跳变，如果差异过大则使用新值
    if history:  # 如果有历史数据
        x_diff = abs(new_value[0] - x_avg) / max(w_avg, 1)
        y_diff = abs(new_value[1] - y_avg) / max(h_avg, 1)

        if x_diff > SMOOTH_THRESHOLD or y_diff > SMOOTH_THRESHOLD:
            # 差异过大，使用新值并重置部分历史
            new_history = new_history[-2:]  # 保留最近2个值
            return new_value, new_history

    return (x_avg, y_avg, w_avg, h_avg), new_history


def detection():
    global history_boxes
    print("det_infer start")
    # 使用json读取内容初始化部署变量
    deploy_conf = read_deploy_config(config_path)
    kmodel_name = deploy_conf["kmodel_path"]
    labels = deploy_conf["categories"]
    confidence_threshold = deploy_conf["confidence_threshold"]
    nms_threshold = deploy_conf["nms_threshold"]
    img_size = deploy_conf["img_size"]
    num_classes = deploy_conf["num_classes"]
    color_four = get_colors(num_classes)
    nms_option = deploy_conf["nms_option"]
    model_type = deploy_conf["model_type"]
    if model_type == "AnchorBaseDet":
        anchors = deploy_conf["anchors"][0] + deploy_conf["anchors"][1] + deploy_conf["anchors"][2]
    kmodel_frame_size = img_size
    frame_size = [OUT_RGB888P_WIDTH, OUT_RGB888P_HEIGH]
    strides = [8, 16, 32]

    # 计算padding值
    top, bottom, left, right, ratio = two_side_pad_param(frame_size, kmodel_frame_size)

    # 初始化kpu
    kpu = nn.kpu()
    kpu.load_kmodel(root_path + kmodel_name)
    # 初始化ai2d
    ai2d = nn.ai2d()
    ai2d.set_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)
    ai2d.set_pad_param(True, [0, 0, 0, 0, top, bottom, left, right], 0, [114, 114, 114])
    ai2d.set_resize_param(True, nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    ai2d_builder = ai2d.build(
        [1, 3, OUT_RGB888P_HEIGH, OUT_RGB888P_WIDTH], [1, 3, kmodel_frame_size[1], kmodel_frame_size[0]]
    )
    # 初始化并配置sensor
    sensor = Sensor()
    sensor.reset()
    # 设置镜像
    sensor.set_hmirror(False)
    # 设置翻转
    sensor.set_vflip(False)
    # 通道0直接给到显示VO，格式为YUV420
    sensor.set_framesize(width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT)
    sensor.set_pixformat(PIXEL_FORMAT_YUV_SEMIPLANAR_420)
    # 通道2给到AI做算法处理，格式为RGB888
    sensor.set_framesize(width=OUT_RGB888P_WIDTH, height=OUT_RGB888P_HEIGH, chn=CAM_CHN_ID_2)
    sensor.set_pixformat(PIXEL_FORMAT_RGB_888_PLANAR, chn=CAM_CHN_ID_2)
    # 绑定通道0的输出到vo
    sensor_bind_info = sensor.bind_info(x=0, y=0, chn=CAM_CHN_ID_0)
    Display.bind_layer(** sensor_bind_info, layer=Display.LAYER_VIDEO1)
    if display_mode == "lcd":
        # 设置为ST7701显示，默认800x480
        Display.init(Display.ST7701, to_ide=True)
    else:
        # 设置为LT9611显示，默认1920x1080
        Display.init(Display.LT9611, to_ide=True)
    # 创建OSD图像
    osd_img = image.Image(DISPLAY_WIDTH, DISPLAY_HEIGHT, image.ARGB8888)
    # media初始化
    MediaManager.init()
    # 启动sensor
    sensor.run()
    rgb888p_img = None
    ai2d_input_tensor = None
    data = np.ones((1, 3, kmodel_frame_size[1], kmodel_frame_size[0]), dtype=np.uint8)
    ai2d_output_tensor = nn.from_numpy(data)

    # 记录当前帧检测到的物体ID
    current_ids = set()

    while True:
        with ScopedTiming("total", False):  # 禁用计时打印
            rgb888p_img = sensor.snapshot(chn=CAM_CHN_ID_2)
            if rgb888p_img.format() == image.RGBP888:
                ai2d_input = rgb888p_img.to_numpy_ref()
                ai2d_input_tensor = nn.from_numpy(ai2d_input)
                # 使用ai2d进行预处理
                ai2d_builder.run(ai2d_input_tensor, ai2d_output_tensor)
                # 设置模型输入
                kpu.set_input_tensor(0, ai2d_output_tensor)
                # 模型推理
                kpu.run()
                # 获取模型输出
                results = []
                for i in range(kpu.outputs_size()):
                    out_data = kpu.get_output_tensor(i)
                    result = out_data.to_numpy()
                    result = result.reshape((result.shape[0] * result.shape[1] * result.shape[2] * result.shape[3]))
                    del out_data
                    results.append(result)
                # 使用aicube模块封装的接口进行后处理
                det_boxes = aicube.anchorbasedet_post_process(
                    results[0],
                    results[1],
                    results[2],
                    kmodel_frame_size,
                    frame_size,
                    strides,
                    num_classes,
                    confidence_threshold,
                    nms_threshold,
                    anchors,
                    nms_option,
                )
                # 绘制结果
                osd_img.clear()
                current_ids.clear()

                if det_boxes:
                    # 打印识别到的物体数量
                    # print(f"检测到 {len(det_boxes)} 个物体")

                    for idx, det_boxe in enumerate(det_boxes):
                        # 物体类别ID和概率
                        class_id = det_boxe[0]
                        confidence = det_boxe[1]
                        # 打印物体识别概率
                        # print(f"{labels[class_id]}: {confidence:.4f}")

                        # 计算原始检测框坐标 (x, y, w, h)
                        x1, y1, x2, y2 = det_boxe[2], det_boxe[3], det_boxe[4], det_boxe[5]
                        x = int(x1 * DISPLAY_WIDTH // OUT_RGB888P_WIDTH)
                        y = int(y1 * DISPLAY_HEIGHT // OUT_RGB888P_HEIGH)
                        w = int((x2 - x1) * DISPLAY_WIDTH // OUT_RGB888P_WIDTH)
                        h = int((y2 - y1) * DISPLAY_HEIGHT // OUT_RGB888P_HEIGH)

                        # 生成唯一ID标识同一物体
                        box_id = f"{class_id}_{idx}"
                        current_ids.add(box_id)

                        # 获取历史数据并应用移动平均滤波
                        history = history_boxes.get(box_id, [])
                        smoothed_box, new_history = moving_average_filter(history, (x, y, w, h))
                        history_boxes[box_id] = new_history

                        # 保存并打印平滑后的坐标
                        smoothed_x, smoothed_y, smoothed_w, smoothed_h = smoothed_box
                        print(f"{labels[class_id]} 坐标: x={smoothed_x}, y={smoothed_y}, w={smoothed_w}, h={smoothed_h}")

                        # 绘制平滑后的框
                        osd_img.draw_rectangle(
                            smoothed_x,
                            smoothed_y,
                            smoothed_w,
                            smoothed_h,
                            color=color_four[class_id][1:]
                        )

                # 清除已经消失的物体的历史记录
                for box_id in list(history_boxes.keys()):
                    if box_id not in current_ids:
                        del history_boxes[box_id]

                Display.show_image(osd_img, 0, 0, Display.LAYER_OSD3)
                gc.collect()
            rgb888p_img = None

    del ai2d_input_tensor
    del ai2d_output_tensor
    # 停止摄像头输出
    sensor.stop()
    # 去初始化显示设备
    Display.deinit()
    # 释放媒体缓冲区
    MediaManager.deinit()
    gc.collect()
    time.sleep(1)
    nn.shrink_memory_pool()
    print("det_infer end")
    return 0


if __name__ == "__main__":
    detection()
