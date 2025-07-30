import time, os, sys
import math

from machine import UART
from machine import FPIOA

from media.sensor import *
from media.display import *
from media.media import *

sensor_id = 2
sensor = None

# 配置引脚
fpioa = FPIOA()
fpioa.set_function(11, FPIOA.UART2_TXD)
fpioa.set_function(12, FPIOA.UART2_RXD)

# 初始化UART2，波特率115200，8位数据位，无校验，1位停止位
uart = UART(UART.UART2, baudrate=115200, bits=UART.EIGHTBITS, parity=UART.PARITY_NONE, stop=UART.STOPBITS_ONE)

# 要发送的字符串
message = "None"

# 颜色阈值设置 - 黑色阈值（低亮度值）
BLACK_THRESHOLD = [(0, 50, -20, 20, -20, 20)]  # 黑色阈值，可根据实际环境调整

# 图像中心（QQVGA）
CENTER_X = 80
CENTER_Y = 60

try:
    # 构造一个具有默认配置的摄像头对象
    sensor = Sensor(id=sensor_id)
    # 重置摄像头sensor
    sensor.reset()

    # 设置垂直翻转
    sensor.set_vflip(False)

    # 设置通道0的输出尺寸
    sensor.set_framesize(Sensor.QVGA, chn=CAM_CHN_ID_0)
    # 设置通道0的输出像素格式为RGB565
    sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_0)

    best_blob = None
    max_blob_area = 0

    # 固定曝光/白平衡，提升识别稳定性
    # sensor.set_auto_gain(False)
    # sensor.set_auto_whitebal(False)

    # 使用IDE的帧缓冲区作为显示输出
    Display.init(Display.VIRT, width=320, height=240, to_ide=True)
    # 初始化媒体管理器
    MediaManager.init()
    # 启动传感器
    sensor.run()



    while True:
        os.exitpoint()

        # 捕获通道0的图像
        img = sensor.snapshot(chn=CAM_CHN_ID_0)

        # 显示捕获的图像
        Display.show_image(img)

        # 直接查找全图中的矩形
        rects = img.find_rects(threshold=3000)  # 可调低点以容忍度高些
        best_rect = None
        max_area = 0
        message = "None"

        for rect in rects:
            r = rect.rect()
            w, h = r[2], r[3]
            area = w * h

            if w < 25 or h < 25:  # 过滤掉非常小的矩形
                continue

            if area > max_area:
                max_area = area
                best_rect = rect

        # 如果找到了最大矩形，就画出来
        if best_rect:
            img.draw_rectangle(best_rect.rect(), color=(0, 255, 0), thickness=2)
            for pt in best_rect.corners():
                img.draw_circle(pt[0], pt[1], 2, color=(255, 0, 255))

            message = str(best_rect.rect())



        # 发送数据
        uart.write(message + "\n")  # 增加换行符，方便接收端解析
        # 显示处理后的图像
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
    #释放UART资源
    uart.deinit()
    # 释放媒体缓冲区
    MediaManager.deinit()
