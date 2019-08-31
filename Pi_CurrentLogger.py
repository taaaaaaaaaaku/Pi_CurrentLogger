## coding: UTF-8

# =======================
# PiZero Current Logger
# Version: Beta1.0
# Maintainer: taaaaaaaaaaku
# =======================
# Usage:
#  1. RaspberryPi Zero に，電流センサボードを接続します。
#  2. RaspberryPi Zero に，所定のUSBメモリを接続します。
#  3. 本プログラムを実行します。
#    ※実行時に，第一引数として debug　を与えると，デバッグ出力を行います。
#    ※実行時に，第一or第二引数として数字を与えると，AMP_PER_LEDをその値で上書きします。
#  4. 電源LEDがONになり，測定電流に合わせて，LEDバーが点灯します。
#  5. タクトスイッチを押すと，電流ロギングを開始します。
#    →ロギング中は，電源LEDが1秒ごとに点滅します。
#    ※USBが認識できないなど，エラーが発生した場合，電源LEDが高速に点灯します。
#  6. もう一度，タクトスイッチを押すと，ログが終了します。
# =======================
# ログデータについて
#  ・ログ開始時刻を元に，yyyymmdd_hhmmss.csvというファイルが生成されます。
#  ・1秒ごとに，計測電流値を書き込みます。
#  ・1時間ごとにファイルが更新され，別ファイルが生成されます。
# =======================

# ライブラリのインポート
import time
import threading
from datetime import datetime,timedelta
import RPi.GPIO as GPIO
import time
import smbus
import sys
import subprocess	#スクリプト実行用ライブラリ

# 各種パラメータ
LOG_PATH = '/media/pi/MYUSB/'   # CSVファイルの記録先フォルダ
LOG_DIGITS = 6                  # ログデータ内での電流値の小数点以下桁数
SHUNT_OHM = 10                  # シャント抵抗値[Ohm]
CT_RATIO = 3000                 # CTセンサ倍率(実電流/センサ出力)
AMP_PER_LED = 5                 # LEDバー内，1LEDあたりの電流値
EFFCT_2_AVRG = 0.9005           # 平滑化電流値から実効値への変換係数
#Note:実効値1Aの正弦波 -> 絶対値処理&平滑化 -> 0.9005A

# ADC設定
ADC_ADDR = 0x68
ADC_VREF = 2.048
ADC_RES = 32768
ADC_CONFIG_VAL = 0b10011000

# GPIOピン番号設定
LED_POW = 21
LED_LV1 = 20
LED_LV2 = 5
LED_LV3 = 6
LED_LV4 = 13
LED_LV5 = 19
LED_LV6 = 26
SW_INPUT = 25

# 関数定義
def swap16(x):
    return (((x << 8) & 0xFF00) | ((x >>8) & 0x00FF))

def sign16(x):
    return ( -(x & 0b1000000000000000) | (x & 0b0111111111111111) )

# Configure I2C
i2c = smbus.SMBus(1)


# =======================
# 電流センサ読み取り用クラス
# =======================
#   センサーの値を読み込みを行う。
#   スレッドとして実行され，kill_fragにTrueが書き込まれるまで，valueに電流値を書込み続ける
class Thread_readSensor(threading.Thread):
    #   初期化
    def __init__(self, val=0):
        # メンバ変数初期化
        threading.Thread.__init__(self)
        self.value = val         # 測定値
        self.kill_flag = False   # スレッド終了用フラグ
        self.isNeedInitialize = True # 通信初期化要否判定

    #   センサ値読込メソッド：本関数を別スレッドにて実行。
    def run(self):
        while not(self.kill_flag):
            # 初回起動時や，通信エラー発生時は初期化処理を実施
            if self.isNeedInitialize == True:
                try:
                    i2c.write_byte(ADC_ADDR,ADC_CONFIG_VAL) #ADCに設定値を書き込み
                except:
                    # 初期化処理失敗
                    self.isNeedInitialize = True
                    print('[ERROR]i2c.write_byte() has failed @ initializing')
                    # LED高速点滅
                    for i in range(3):
                        GPIO.output(LED_POW,GPIO.LOW)
                        time.sleep(0.1)
                        GPIO.output(LED_POW,GPIO.HIGH)
                        time.sleep(0.1)
                else:
                    # 初期化処理成功
                    self.isNeedInitialize = False;

            # 初期化処理が完了している時のみ，受信処理を実行
            if self.isNeedInitialize != True:
                try:
                    data = i2c.read_word_data(ADC_ADDR,ADC_CONFIG_VAL)  # ADCからデータ取得
                    raw = swap16(int(hex(data),16))                     # エンディアン変更
                    raw_s = sign16(int(hex(raw),16))                    # 符号付きデータに変換
            
            
                    amp = abs(round((ADC_VREF * raw_s / ADC_RES) / SHUNT_OHM * CT_RATIO / EFFCT_2_AVRG, LOG_DIGITS))
                    self.value = amp
                except:
                    self.isNeedInitialize = True
                    print('[ERROR]i2c.read_word_data() has failed @ reading')
                    # LED高速点滅
                    for i in range(3):
                        GPIO.output(LED_POW,GPIO.LOW)
                        time.sleep(0.1)
                        GPIO.output(LED_POW,GPIO.HIGH)
                        time.sleep(0.1)
            # 次ループ実施まで若干待機
            time.sleep(0.1)
        print('Thread_readSensor has finished')

    #   スレッド終了用関数
    def endThread(self):
        self.kill_flag = True
    
# =======================
# CSVファイルの書き込み制御用クラス
# =======================
#   CSVファイルの書込み制御および，書込みに伴うPOW_LEDの制御を行う。
class Thread_writeCSV(threading.Thread):
    #   初期化関数
    def __init__(self):
        # メンバ変数初期化
        threading.Thread.__init__(self)
        self.curFileTime = datetime.now()   # ファイル開始時刻
        self.curRecTime = datetime.now()    # ファイル内での現在時刻
        self.kill_flag = False              # スレッド終了用フラグ
        self.isRecording = False            # ロギング中かどうかを示すフラグ
        self.isLEDon = True                 # ロギング中，LEDの点滅のため，現在のLED点灯状態を保持
        self.file = ''                      # 出力ファイル用ファイルディスクリプタ

    #   記録開始：
    #   －現在日時を元に記録ファイルを生成
    def startRecording(self):
        self.curFileTime = datetime.now()
        self.curRecTime = self.curFileTime - timedelta(seconds=1)
        temp_fileName = LOG_PATH + self.curFileTime.strftime("%Y%m%d_%H%M%S.csv")
        try:
            self.file = open(temp_fileName, mode='w')  #出力ファイルオープン
        except:
            # エラー発生：エラーメッセージを生成し，LEDを高速で点滅させる。
            print('[Error]Following Output file cannot be created')
            print(temp_fileName)
            # LED高速点滅
            for i in range(5):
                GPIO.output(LED_POW,GPIO.LOW)
                time.sleep(0.1)
                GPIO.output(LED_POW,GPIO.HIGH)
                time.sleep(0.1)
                
        else:
            # 出力ファイル生成に成功：ヘッダを書き込み
            self.file.write('day[yyyy/mm/dd],time[hh:mm:ss],current[A]\n')
            self.isRecording = True

    #   ファイル更新：
    #   －現在記録中のCSVファイルを終了し，新たなファイルで記録を開始
    def refreshRecordingFile(self):
        self.endRecording();
        self.startRecording();

    #   記録終了
    def endRecording(self):
        # ロギング中の時のみ実行
        if self.isRecording != False:
            try:
                self.file.close()
            except:
                print('[Error]Closing file discripter has failed. USB memory may removed already.")
            self.isRecording = False
            GPIO.output(LED_POW,GPIO.HIGH) # POW_LEDを常時点灯へ
        # unmount USB
        cmd=["umount",LOG_PATH]
        subprocess.call(cmd)

    #   ロギングデータ出力処理：本関数を別スレッドにて実行
    def run(self):
        while not(self.kill_flag):
            if self.isRecording == True:
                # 現在時刻を一時的に取得
                curTime = datetime.now()
                
                # 毎時処理：ファイル更新
                if self.curRecTime.hour != curTime.hour:
                    self.refreshRecordingFile()

                
                # 毎秒処理:ロギングデータ追加＆LED点滅
                if self.curRecTime.second != curTime.second:
                    # ロギングデータ追加
                    temp_str = curTime.strftime("%Y/%m/%d") + ',' + curTime.strftime("%H:%M:%S") + ',' + str(sensorThread.value) + '\n' 
                    # 書込み実行
                    try:
                        self.file.write(temp_str)
                    except:
                        print('[Error]Logging to USB failed @' + curTime.strftime("%Y/%m/%d/%H:%M:%S")
                    
                    # LED点滅
                    if self.isLEDon == True:
                        GPIO.output(LED_POW,GPIO.LOW)
                        self.isLEDon = False
                    else:
                        GPIO.output(LED_POW,GPIO.HIGH)
                        self.isLEDon = True
                        
                # 現在時刻データ更新
                self.curRecTime = curTime
            
        print('Thread_writeCSV has finished')
        
    #   スレッド終了用関数
    def endThread(self):
        self.kill_flag = True        


if __name__ == "__main__":

    args = sys.argv

    # 引数に指定があった場合，LEDバーの電流値を変更
    if len(args) > 1 :
        try:
            # 第一引数に数字があれば取得
            AMP_PER_LED = int(args[1])
        except:
            if len(args) > 2 :
                try:
                    # 第一引数に数字があれば取得
                    AMP_PER_LED = int(args[2])
                except:
                    print('Cannot read AMP_PER_LED')
                        
                
    
    print('ProgramStart')    
    try:
        # GPIO設定
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LED_POW, GPIO.OUT)
        GPIO.setup(LED_LV1, GPIO.OUT)
        GPIO.setup(LED_LV2, GPIO.OUT)
        GPIO.setup(LED_LV3, GPIO.OUT)
        GPIO.setup(LED_LV4, GPIO.OUT)
        GPIO.setup(LED_LV5, GPIO.OUT)
        GPIO.setup(LED_LV6, GPIO.OUT)
        GPIO.setup(SW_INPUT, GPIO.IN)

        # LED_POWを点灯
        GPIO.output(LED_POW,GPIO.HIGH)
        
        # スレッド定義
        sensorThread = Thread_readSensor()
        fileThread = Thread_writeCSV()

        # スレッド開始
        sensorThread.start()
        fileThread.start()
        
        print_cnt = 0
        while 1:
            # 電流値取得
            amp = sensorThread.value
            # [debugモード時]定期的にコンソールに電流値出力
            if len(args) > 1 :
                if args[1] == 'debug':
                    if print_cnt % 10 == 0:
                        print ('Current:'+str(amp)+ ' A')
                    print_cnt += 1

            # LEDバーを，電流値に応じて点灯
            GPIO.output(LED_LV1, GPIO.LOW)
            GPIO.output(LED_LV2, GPIO.LOW)
            GPIO.output(LED_LV3, GPIO.LOW)
            GPIO.output(LED_LV4, GPIO.LOW)
            GPIO.output(LED_LV5, GPIO.LOW)
            GPIO.output(LED_LV6, GPIO.LOW)
            if(amp > AMP_PER_LED * 1):
                GPIO.output(LED_LV1, GPIO.HIGH)
            if(amp > AMP_PER_LED * 2):
                GPIO.output(LED_LV2, GPIO.HIGH)
            if(amp > AMP_PER_LED * 3):
                GPIO.output(LED_LV3, GPIO.HIGH)
            if(amp > AMP_PER_LED * 4):
                GPIO.output(LED_LV4, GPIO.HIGH)
            if(amp > AMP_PER_LED * 5):
                GPIO.output(LED_LV5, GPIO.HIGH)
            if(amp > AMP_PER_LED * 6):
                GPIO.output(LED_LV6, GPIO.HIGH)

            # タクトスイッチ押下時：記録開始＆終了
            if GPIO.input(SW_INPUT) == GPIO.LOW:
                # スイッチが離されるまで待機
                while GPIO.input(SW_INPUT) == GPIO.LOW:
                    time.sleep(0.05)
                # チャタリングの影響を避けるため，一時的にスリープ                    
                time.sleep(0.2)

                # 現在のロギング状況に応じて，記録開始or終了
                if fileThread.isRecording == False:
                    fileThread.startRecording()
                else:
                    fileThread.endRecording()
                    
            # 次ループ実行までスリープ    
            time.sleep(0.1)


    except KeyboardInterrupt:
        print('Program cancelled!!')

    finally:
        # 終了処理

        # GPIOをクローズ
        GPIO.cleanup() 

        # 各スレッドに終了を命令
        sensorThread.endThread()
        fileThread.endThread()
        # 各スレッドの終了を待機
        sensorThread.join()
        fileThread.join()
        print('Program Finished')
