## coding: UTF-8
# =======================
# PiZero Current Logger
# Version: Beta1.0
# Maintainer: taaaaaaaaaaku
# =======================
# ライブラリのインポート
import time
import threading
from datetime import datetime,timedelta
import RPi.GPIO as GPIO
import smbus
import sys
import array
import subprocess	#スクリプト実行用ライブラリ
import pychromecast     #GoogleHome用
# from gtts import gTTS

# 各種パラメータ
LOG_PATH = '/media/pi/MYUSB/'   # CSVファイルの記録先フォルダ
LOG_DIGITS = 6                  # ログデータ内での電流値の小数点以下桁数
SHUNT_OHM = 10                  # シャント抵抗値[Ohm]
CT_RATIO = 3000                 # CTセンサ倍率(実電流/センサ出力)
AMP_PER_LED = 3                 # LEDバー内，1LEDあたりの電流値
EFFCT_2_AVRG = 0.9005           # 平滑化電流値から実効値への変換係数
#Note:実効値1Aの正弦波 -> 絶対値処理&平滑化 -> 0.9005A
BUZZER_AMP = 25                 # ブザーを鳴らす電流
GOOGLE_HOME_IP_ADDR = '192.168.11.18' # 音声警告を出したいGoogleHomeデバイスのアドレス
OC_WARNING_DATA_PATH = 'http://192.168.11.10/openAccess/OC_warning.mp3' #警告音声の格納先


# ADC設定
ADC_ADDR = 0x68
ADC_VREF = 2.048
ADC_RES = 32768
# bit7  : /RDY(1で変換開始)
# bit6,5: チャネル選択(00:ch1 01:ch2 10:ch3 11:ch4)
# bit4  : 変換モード(1:ワンショット 0:連続)
# bit3,2: サンプリングレート(11:3.75SPS/18bit 10:15SPS/16bit 01:60SPS/14bit 00:240SPS/12bit)
# bit1,0: PGAゲイン(00:x1 01:x2 10:x4 11:x8)
ADC_CONFIG_CH1 = 0b11011000 # ボード上CH1 = MCP3424のch3
ADC_CONFIG_CH2 = 0b11111000 # ボード上CH2 = MCP3424のch4
ADC_CONFIG_CH3 = 0b10011000 # ボード上CH3 = MCP3424のch1
ADC_CONFIG_CH4 = 0b10111000 # ボード上CH4 = MCP3424のch2

# GPIOピン番号設定
LED_BAR = array.array('i', [18, 23, 24, 25, 8, 7, 12, 16, 20, 21])
LED_POW = 5
SW_INPUT = 19
BUZZER= 26

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
        self.value = array.array('f', [val, val, val, val]) # 測定値
        self.kill_flag = False       # スレッド終了用フラグ
        self.isNeedInitialize = True # 通信初期化要否判定

    #   センサ値読込メソッド：本関数を別スレッドにて実行。
    def run(self):
        while not(self.kill_flag):
            # 初回起動時や，通信エラー発生時は初期化処理を実施
            if self.isNeedInitialize == True:
                try:
                    i2c.write_byte(ADC_ADDR,ADC_CONFIG_CH1) #ADCに試しに設定値を書き込み
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
                    data = array.array('i')

                    #ch1
                    i2c.write_byte(ADC_ADDR,ADC_CONFIG_CH1)
                    time.sleep(0.2)
                    data.append(i2c.read_word_data(ADC_ADDR,0x00))  # ADCからデータ取得
                    #ch2
                    i2c.write_byte(ADC_ADDR,ADC_CONFIG_CH2)
                    time.sleep(0.2)
                    data.append(i2c.read_word_data(ADC_ADDR,0x00))  # ADCからデータ取得
                    #ch3
                    i2c.write_byte(ADC_ADDR,ADC_CONFIG_CH3)
                    time.sleep(0.2)
                    data.append(i2c.read_word_data(ADC_ADDR,0x00))  # ADCからデータ取得
                    #ch4
                    i2c.write_byte(ADC_ADDR,ADC_CONFIG_CH4)
                    time.sleep(0.2)
                    data.append(i2c.read_word_data(ADC_ADDR,0x00))  # ADCからデータ取得

                    for i in range(4):
                        raw = swap16(int(hex(data[i]),16))                     # エンディアン変更
                        raw_s = sign16(int(hex(raw),16))                    # 符号付きデータに変換
                        amp = abs(round((ADC_VREF * raw_s / ADC_RES) / SHUNT_OHM * CT_RATIO / EFFCT_2_AVRG, LOG_DIGITS))
                        self.value[i] = amp
                except:
                    self.isNeedInitialize = True
                    print('[ERROR]sensor read error@ reading')
                    # LED高速点滅
                    for i in range(3):
                        GPIO.output(LED_POW,GPIO.LOW)
                        time.sleep(0.1)
                        GPIO.output(LED_POW,GPIO.HIGH)
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
            self.file.write('day[yyyy/mm/dd],time[hh:mm:ss],ch1[A],ch2[A],ch3[A],ch4[A]\n')
            self.isRecording = True

    #   ファイル更新：
    #   －現在記録中のCSVファイルを終了し，新たなファイルで記録を開始
    def refreshRecordingFile(self):
        self.endRecording(False);   # アンマウントはせずにファイル書込みを終了
        self.startRecording();

    #   記録終了
    def endRecording(self,is_umount=True):
        # ロギング中の時のみ実行
        if self.isRecording != False:
            try:
                self.file.close()
            except:
                print('[Error]Closing file discripter has failed. USB memory may removed already.')
            self.isRecording = False
            GPIO.output(LED_POW,GPIO.HIGH) # POW_LEDを常時点灯へ

            # unmount USB
            if is_umount == True:
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
                    temp_str = curTime.strftime("%Y/%m/%d") + ',' + curTime.strftime("%H:%M:%S") + ',' + str(sensorThread.value[0]) + ',' + str(sensorThread.value[1]) + ',' + str(sensorThread.value[2]) + ',' + str(sensorThread.value[3]) + '\n' 
                    # 書込み実行
                    try:
                        self.file.write(temp_str)
                    except:
                        print('[Error]Logging to USB failed @' + curTime.strftime("%Y/%m/%d/%H:%M:%S"))
                    
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

# =======================
# ブザー管理クラス
# =======================
#   ブザーの制御を行う。
class Thread_buzzerMgr(threading.Thread):
    #   初期化関数
    def __init__(self):
        # メンバ変数初期化
        threading.Thread.__init__(self)
        self.kill_flag = False              # スレッド終了用フラグ
        self.isOverCurrent = False         # OC発生状況
        self.manualBuzzerTime = 0.0        # 手動鳴動時間

        # ブザーGPIOを初期化
        GPIO.setup(BUZZER, GPIO.OUT)       
        GPIO.output(BUZZER, GPIO.LOW)
        
    #   過電流発生状況をセット
    def setOverCurrent(self, isOC):
        self.isOverCurrent = isOC

    #   指定時間鳴動をセット
    def setManual(self, duration):
        self.manualBuzzerTime = duration
        
    #   ロギングデータ出力処理：本関数を別スレッドにて実行
    def run(self):
        while not(self.kill_flag):
            # 電流警告
            if(self.isOverCurrent):
                GPIO.output(BUZZER, GPIO.HIGH)
                time.sleep(0.2)
                GPIO.output(BUZZER, GPIO.LOW)
                time.sleep(0.2)
                GPIO.output(BUZZER, GPIO.HIGH)
                time.sleep(0.2)
                GPIO.output(BUZZER, GPIO.LOW)
                time.sleep(0.2)
                GPIO.output(BUZZER, GPIO.HIGH)
                time.sleep(0.2)
                GPIO.output(BUZZER, GPIO.LOW)
                time.sleep(5)
            # マニュアル鳴動
            if (self.manualBuzzerTime != 0.0):
                GPIO.output(BUZZER, GPIO.HIGH)
                time.sleep(self.manualBuzzerTime)
                GPIO.output(BUZZER, GPIO.LOW)
                self.manualBuzzerTime = 0.0
                

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
                    # 第二引数に数字があれば取得
                    AMP_PER_LED = int(args[2])
                except:
                    print('Cannot read AMP_PER_LED')
                        
                
    
    print('ProgramStart')    
    try:
        # GPIO設定
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LED_POW, GPIO.OUT)
        for pin in LED_BAR:
            GPIO.setup(pin, GPIO.OUT)
        GPIO.setup(SW_INPUT, GPIO.IN)
        
        # スレッド定義
        sensorThread = Thread_readSensor()
        fileThread = Thread_writeCSV()
        buzzerThread = Thread_buzzerMgr()
        
        # スレッド開始
        sensorThread.start()
        fileThread.start()
        buzzerThread.start()

        # LED_POWを点灯
        GPIO.output(LED_POW,GPIO.HIGH)

        # 1秒間全ＬＥＤ点灯＋ブザー0.2秒鳴動
        for pin in LED_BAR:
            GPIO.output(pin, GPIO.HIGH)
        time.sleep(1.0)
        for pin in LED_BAR:
            GPIO.output(pin, GPIO.LOW)
        buzzerThread.setManual(0.2)

        
        print_cnt = 0
        while 1:
            # 電流値取得
            amp = sensorThread.value[0]
            # [debugモード時]定期的にコンソールに電流値出力
            if len(args) > 1 :
                if args[1] == 'debug':
                    if print_cnt % 10 == 0:
                        print ('Current:'+format(amp, '.2f')+ ' A')
                    print_cnt += 1

            # LEDバーを，電流値に応じて点灯
            # TODO:Ch1と2の合計など，設定によって複数チャンネル合計でのLED表示に対応
            for i in range( len(LED_BAR) ):
                if(amp > AMP_PER_LED * i):
                    GPIO.output(LED_BAR[i], GPIO.HIGH)
                else:
                    GPIO.output(LED_BAR[i], GPIO.LOW)
            # 電流が設定値を超えた場合，ブザー鳴動 & GoogleHomeで音声出力
            if(amp > BUZZER_AMP):
                buzzerThread.setOverCurrent(True)
                try:
                    #IPアドレスで特定する
                    googleHome = pychromecast.Chromecast(GOOGLE_HOME_IP_ADDR)

                    if not googleHome.is_idle:
                        print("Killing current running app")
                        googleHome.quit_app()
                        time.sleep(5)

                    #喋らせる
                    googleHome.wait()
                    googleHome.media_controller.play_media(OC_WARNING_DATA_PATH, 'audio/mp3')
                    googleHome.media_controller.block_until_active()
            else:
                buzzerThread.setOverCurrent(False)

            

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
        buzzerThread.endThread()
        # 各スレッドの終了を待機
        sensorThread.join()
        fileThread.join()
        buzzerThread.join()
        print('Program Finished')
