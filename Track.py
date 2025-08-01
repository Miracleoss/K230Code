import time, os, sys
import math
import ustruct

from machine import UART
from machine import FPIOA

from media.sensor import *
from media.display import *
from media.media import *
from image import Image  # 补充这一行，导入Image类

sensor_id = 2
sensor = None

# 配置引脚
fpioa = FPIOA()
fpioa.set_function(11, FPIOA.UART2_TXD)
fpioa.set_function(12, FPIOA.UART2_RXD)

# 初始化UART2，波特率115200，8位数据位，无校验，2位停止位
uart = UART(UART.UART2, baudrate=115200, bits=UART.EIGHTBITS, parity=UART.PARITY_NONE, stop=UART.STOPBITS_TWO)

# 要发送的字符串
message = "None"

# 颜色阈值设置 - 黑色阈值（低亮度值）
BLACK_THRESHOLD = [(3, 21, -17, 6, -9, 9)]  # 黑色阈值，可根据实际环境调整

# 图像中心（QQVGA）
CENTER_X = 80
CENTER_Y = 60

# 跟踪状态变量
last_rect = None         # 上一次矩形信息 (x, y, w, h)
rect_skip_count = 0      # 矩形丢失计数器
MAX_RECT_SKIP = 5        # 最大允许丢失帧数
ALPHA = 0.6              # 移动平均滤波系数，值越大响应越快

# 矩形验证参数
MIN_RECT_AREA = 625      # 25x25的最小面积
MAX_RECT_AREA = 320*240//5  # 最大面积不超过屏幕的一半

RECT_ASPECT_RATIO_TOLERANCE = 0.1  # 宽高比容忍度

# 串口通信协议定义
FRAME_HEADER1 = 0xA5
FRAME_HEADER2 = 0xA6
FRAME_FOOTER = 0x5B


def is_valid_rectangle(rect):
    """验证矩形是否符合要求"""
    r = rect.rect()
    x, y, w, h = r

    # 面积检查
    area = w * h
    if area < MIN_RECT_AREA or area > MAX_RECT_AREA:  # 上限 下限同时检查
            return False

    # 宽高比检查
    if min(w, h) == 0:
        return False
    aspect_ratio = max(w, h) / min(w, h)
    if aspect_ratio > (2 + RECT_ASPECT_RATIO_TOLERANCE):
        return False

    # 角点检查
    corners = rect.corners()
    if len(corners) != 4:
        return False

    return True

def rect_center(rect):
    """计算矩形中心坐标"""
    r = rect.rect()
    x, y, w, h = r
    return (x + w//2, y + h//2)

def send_rect_data(x, y, w, h):
    """发送矩形数据封装成固定格式帧"""
    global uart
    # 打包数据为8字节: 帧头1, 帧头2, x, y, w, h, 保留位, 帧尾
    data = ustruct.pack("<BBHHHHB",
                        FRAME_HEADER1,
                        FRAME_HEADER2,
                        int(x),       # x坐标(2字节)
                        int(y),       # y坐标(2字节)
                        int(w),       # 宽度(2字节)
                        int(h),       # 高度(2字节)
                        FRAME_FOOTER
                       )
    uart.write(data)
    # 调试信息
    print(f"发送矩形数据: x={x}, y={y}, w={w}, h={h}")

def send_no_rect_data():
    """发送未检测到矩形的数据"""
    global uart
    # 全部置0表示未检测到矩形
    data = ustruct.pack("<BBHHHHB",
                        FRAME_HEADER1,
                        FRAME_HEADER2,
                        0, 0, 0, 0,  # x, y, w, h全部为0
                        FRAME_FOOTER
                       )
    uart.write(data)
    print("未检测到矩形")

try:
    # 构造一个具有默认配置的摄像头对象
    sensor = Sensor(id=sensor_id)
    # 重置摄像头sensor
    sensor.reset()

    # 设置翻转
    # sensor.set_vflip(False)
    # sensor.set_vflip(True)
    sensor.set_hmirror(True)

    # 设置通道0的输出尺寸
    sensor.set_framesize(Sensor.QVGA, chn=CAM_CHN_ID_0)
    # 设置通道0的输出像素格式为RGB565
    sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_0)


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
        # Display.show_image(img)

        # 快速降噪处理
        # 快速降噪处理（完全兼容的方案）
        # 先过滤出黑色
        # img = img.binary(BLACK_THRESHOLD)
        img = img.erode(2)  # 第一步：腐蚀，去除小噪点和干扰边缘
        # img = img.dilate(1)  # 第二步：膨胀，恢复矩形的完整轮廓



        # 查找矩形
        rects = img.find_rects(threshold=5500)  # 调整阈值，减少误检测
        best_rect = None
        max_area = 0


        # 寻找最佳矩形
        for rect in rects:
            if is_valid_rectangle(rect):
                r = rect.rect()
                area = r[2] * r[3]
                if area > max_area:
                    max_area = area
                    best_rect = rect

       # 矩形跟踪逻辑
        current_rect = None

        if best_rect:
            # 找到有效矩形
            current_rect = best_rect.rect()
            rect_skip_count = 0  # 重置丢失计数器

            # 应用移动平均滤波
            if last_rect:
                 # 对矩形参数进行平滑处理
                filtered_rect = (
                    int(ALPHA * current_rect[0] + (1 - ALPHA) * last_rect[0]),
                    int(ALPHA * current_rect[1] + (1 - ALPHA) * last_rect[1]),
                    int(ALPHA * current_rect[2] + (1 - ALPHA) * last_rect[2]),
                    int(ALPHA * current_rect[3] + (1 - ALPHA) * last_rect[3])
                )
                last_rect = filtered_rect
            else:
                # 首次检测到矩形
                last_rect = current_rect

             # 绘制矩形和中心点
            x, y, w, h = last_rect
            center_x, center_y = x + w//2, y + h//2
            img.draw_rectangle(last_rect, color=(0, 255, 0), thickness=2)
            img.draw_circle(center_x, center_y, 3, color=(255, 0, 0), thickness=2)

            send_rect_data(x, y, w, h)
        else:
            # 未找到矩形
            rect_skip_count += 1
            if rect_skip_count <= MAX_RECT_SKIP and last_rect:
                # 在允许丢失帧数内，继续使用上一次的矩形信息
                x, y, w, h = last_rect
                img.draw_rectangle(last_rect, color=(0, 255, 0), thickness=2)
                send_rect_data(x, y, w, h)
            else:
                # 超过最大丢失帧数，重置跟踪并发送无矩形数据
                last_rect = None
                send_no_rect_data()

        # 显示处理后的图像
        img.compressed_for_ide()
        Display.show_image(img)
        # print(f"发送矩形数据: x={x}, y={y}, w={w}, h={h}")

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
