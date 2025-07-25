# 立创·庐山派-K230-CanMV开发板资料与相关扩展板软硬件资料官网全部开源
# 开发板官网：www.lckfb.com
# 技术支持常驻论坛，任何技术问题欢迎随时交流学习
# 立创论坛：www.jlc-bbs.com/lckfb
# 关注bilibili账号：【立创开发板】，掌握我们的最新动态！
# 不靠卖板赚钱，以培养中国工程师为己任

import time, os, sys
import math

from media.sensor import *
from media.display import *
from media.media import *

sensor_id = 2
sensor = None

# 颜色阈值设置
WHITE_THRESHOLD = ((62, 100, -10, 0, -2, 4))  # 白色阈值，可根据实际环境调整

try:
    # 构造一个具有默认配置的摄像头对象
    sensor = Sensor(id=sensor_id)
    # 重置摄像头sensor
    sensor.reset()

    # 无需进行镜像翻转
    # 设置水平镜像
    # sensor.set_hmirror(False)
    # 设置垂直翻转
    sensor.set_vflip(False)

    # 设置通道0的输出尺寸
    sensor.set_framesize(Sensor.QQVGA, chn=CAM_CHN_ID_0)
    # 设置通道0的输出像素格式为RGB888
    sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_0)

    # 使用IDE的帧缓冲区作为显示输出
    Display.init(Display.VIRT, width=160, height=120, to_ide=True)
    # 初始化媒体管理器
    MediaManager.init()
    # 启动传感器
    sensor.run()
    # 跟踪状态变量
    green_blod = None
    red_blob = None
    white_blob = None
    last_white_center = None
    white_skip_count = 0
    MAX_WHITE_SKIP = 5  # 白色目标最大丢失帧数
    ALPHA = 0.3         # 移动平均滤波系数

    def is_valid_blob(blob, min_area=300):
        return blob.pixels() > min_area and blob.density() > 0.2

    while True:
        os.exitpoint()

        # 捕获通道0的图像
        img = sensor.snapshot(chn=CAM_CHN_ID_0)
        # 显示捕获的图像
        Display.show_image(img)

        #开始
        white_blobs = img.find_blobs([WHITE_THRESHOLD], pixels_threshold=300, area_threshold=300, merge=True)
        # 选择最大的有效白色色块
        largest_white = None
        for blob in white_blobs:
            if largest_white is None or blob.pixels() > largest_white.pixels():
               largest_white = blob

        # 白色目标跟踪逻辑
        if largest_white:
            # 计算当前白色目标中心
            current_center = (largest_white.cx(), largest_white.cy())

            # 应用移动平均滤波
            if last_white_center:
                filtered_center = (
                    ALPHA * current_center[0] + (1 - ALPHA) * last_white_center[0],
                    ALPHA * current_center[1] + (1 - ALPHA) * last_white_center[1]
                )
                last_white_center = filtered_center
            else:
                last_white_center = current_center



            # 更新白色目标状态
            white_blob = largest_white
            white_skip_count = 0
            # 绘制白色目标
            img.draw_rectangle(white_blob.rect(), color=(255, 255, 255))
            img.draw_cross(int(last_white_center[0]), int(last_white_center[1]), color=(255, 255, 255))
            # img.draw_string(white_blob.cx()+10, white_blob.cy(), "White", color=(255, 255, 255))

            img.compressed_for_ide()
            Display.show_image(img)

except KeyboardInterrupt as e:
    print("用户停止: ", e)
except BaseException as e:
    print(f"用户手动异常: {e}")
finally:
    # 停止传感器运行
    if isinstance(sensor, Sensor):
        sensor.stop()
    # 反初始化显示模块
    Display.deinit()
    os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
    time.sleep_ms(100)
    # 释放媒体缓冲区
    MediaManager.deinit()
