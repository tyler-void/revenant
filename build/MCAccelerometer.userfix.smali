.class public Lcom/miniclip/input/MCAccelerometer;
.super Lcom/miniclip/framework/AbstractActivityListener;
.source "MCAccelerometer.java"

# interfaces
.implements Landroid/hardware/SensorEventListener;


# static fields
.field private static display:Landroid/view/Display;

.field private static instance:Lcom/miniclip/input/MCAccelerometer;

.field private static isEnabled:Z

.field private static isRegistered:Z

.field private static mAccelerometer:Landroid/hardware/Sensor;

.field private static mHasWindowFocus:Z

.field private static mResumeOnFocus:Z

.field private static mRotation:I

.field private static mSensorManager:Landroid/hardware/SensorManager;


# direct methods
.method static constructor <clinit>()V
    .locals 2

    .prologue
    const/4 v1, 0x0

    .line 21
    new-instance v0, Lcom/miniclip/input/MCAccelerometer;

    invoke-direct {v0}, Lcom/miniclip/input/MCAccelerometer;-><init>()V

    sput-object v0, Lcom/miniclip/input/MCAccelerometer;->instance:Lcom/miniclip/input/MCAccelerometer;

    .line 25
    sput v1, Lcom/miniclip/input/MCAccelerometer;->mRotation:I

    .line 26
    const/4 v0, 0x0

    sput-object v0, Lcom/miniclip/input/MCAccelerometer;->display:Landroid/view/Display;

    .line 27
    sput-boolean v1, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z

    .line 28
    sput-boolean v1, Lcom/miniclip/input/MCAccelerometer;->isRegistered:Z

    .line 107
    sput-boolean v1, Lcom/miniclip/input/MCAccelerometer;->mHasWindowFocus:Z

    .line 108
    sput-boolean v1, Lcom/miniclip/input/MCAccelerometer;->mResumeOnFocus:Z

    return-void
.end method

.method private constructor <init>()V
    .locals 0

    .prologue
    .line 31
    invoke-direct {p0}, Lcom/miniclip/framework/AbstractActivityListener;-><init>()V

    return-void
.end method

.method public static init(Lcom/miniclip/framework/MiniclipAndroidActivity;)V
    .locals 4
    .param p0, "activity"    # Lcom/miniclip/framework/MiniclipAndroidActivity;

    .prologue
    const/4 v3, 0x1

    .line 35
    sput v3, Lcom/miniclip/input/MCAccelerometer;->mRotation:I

    .line 36
    const-string v2, "sensor"

    invoke-virtual {p0, v2}, Lcom/miniclip/framework/MiniclipAndroidActivity;->getSystemService(Ljava/lang/String;)Ljava/lang/Object;

    move-result-object v2

    check-cast v2, Landroid/hardware/SensorManager;

    sput-object v2, Lcom/miniclip/input/MCAccelerometer;->mSensorManager:Landroid/hardware/SensorManager;

    .line 37
    sget-object v2, Lcom/miniclip/input/MCAccelerometer;->mSensorManager:Landroid/hardware/SensorManager;

    invoke-virtual {v2, v3}, Landroid/hardware/SensorManager;->getDefaultSensor(I)Landroid/hardware/Sensor;

    move-result-object v2

    sput-object v2, Lcom/miniclip/input/MCAccelerometer;->mAccelerometer:Landroid/hardware/Sensor;

    .line 38
    const-string v2, "window"

    invoke-virtual {p0, v2}, Lcom/miniclip/framework/MiniclipAndroidActivity;->getSystemService(Ljava/lang/String;)Ljava/lang/Object;

    move-result-object v1

    check-cast v1, Landroid/view/WindowManager;

    .line 40
    .local v1, "windowManager":Landroid/view/WindowManager;
    :try_start_0
    invoke-interface {v1}, Landroid/view/WindowManager;->getDefaultDisplay()Landroid/view/Display;

    move-result-object v2

    sput-object v2, Lcom/miniclip/input/MCAccelerometer;->display:Landroid/view/Display;

    .line 41
    sget-object v2, Lcom/miniclip/input/MCAccelerometer;->display:Landroid/view/Display;

    invoke-virtual {v2}, Landroid/view/Display;->getRotation()I

    move-result v2

    sput v2, Lcom/miniclip/input/MCAccelerometer;->mRotation:I
    :try_end_0
    .catch Ljava/lang/NoSuchMethodError; {:try_start_0 .. :try_end_0} :catch_0

    .line 47
    :goto_0
    sget-object v2, Lcom/miniclip/input/MCAccelerometer;->instance:Lcom/miniclip/input/MCAccelerometer;

    invoke-virtual {p0, v2}, Lcom/miniclip/framework/MiniclipAndroidActivity;->addListener(Lcom/miniclip/framework/ActivityListener;)Z

    .line 48
    return-void

    .line 43
    :catch_0
    move-exception v0

    .line 44
    .local v0, "e":Ljava/lang/NoSuchMethodError;
    invoke-virtual {v0}, Ljava/lang/NoSuchMethodError;->printStackTrace()V

    goto :goto_0
.end method

.method private static native onSensorChanged(FFFJ)V
.end method

.method private register()V
    .locals 3

    .prologue
    # 1. Chuẩn bị số 1 (True)
    const/4 v2, 0x1

    .line 60
    # 2. Kiểm tra xem đã đăng ký chưa (nếu rồi thì thôi)
    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->isRegistered:Z
    if-nez v0, :cond_0

    # --- ĐOẠN QUAN TRỌNG NHẤT ---
    # Tôi ĐÃ XÓA dòng kiểm tra "isEnabled" ở đây.
    # Bất kể game có cho phép hay không, ta vẫn cứ đăng ký!
    # -----------------------------

    .line 61
    # 3. Đánh dấu "Đã đăng ký" để không gọi lại lần sau
    sput-boolean v2, Lcom/miniclip/input/MCAccelerometer;->isRegistered:Z

    .line 62
    sget-object v0, Lcom/miniclip/input/MCAccelerometer;->mSensorManager:Landroid/hardware/SensorManager;
    sget-object v1, Lcom/miniclip/input/MCAccelerometer;->mAccelerometer:Landroid/hardware/Sensor;

    # 4. Chọn tốc độ sensor.
    # v2 = 1 (SENSOR_DELAY_GAME) là chuẩn nhất cho game này.
    const/4 v2, 0x1

    # 5. Gửi lệnh đăng ký cho hệ thống
    invoke-virtual {v0, p0, v1, v2}, Landroid/hardware/SensorManager;->registerListener(Landroid/hardware/SensorEventListener;Landroid/hardware/Sensor;I)Z

    .line 64
    :cond_0
    return-void
.end method

.method public static setEnabled(Z)V
    .locals 1
    .param p0, "enabled"    # Z

    .prologue
    .line 51
    sput-boolean p0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z

    .line 52
    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z

    if-eqz v0, :cond_0

    .line 53
    sget-object v0, Lcom/miniclip/input/MCAccelerometer;->instance:Lcom/miniclip/input/MCAccelerometer;

    invoke-direct {v0}, Lcom/miniclip/input/MCAccelerometer;->register()V

    .line 57
    :goto_0
    return-void

    .line 55
    :cond_0
    sget-object v0, Lcom/miniclip/input/MCAccelerometer;->instance:Lcom/miniclip/input/MCAccelerometer;

    invoke-direct {v0}, Lcom/miniclip/input/MCAccelerometer;->unregister()V

    goto :goto_0
.end method

.method private unregister()V
    .locals 1

    .prologue
    # --- MODIFIED BY GEMINI ---
    # Ta vô hiệu hóa hàm này.
    # Khi game gọi "Tắt sensor đi", ta chỉ "return" và không làm gì cả.
    # Sensor sẽ tiếp tục chạy ngầm.

    return-void
.end method


# virtual methods
.method public onAccuracyChanged(Landroid/hardware/Sensor;I)V
    .locals 0
    .param p1, "sensor"    # Landroid/hardware/Sensor;
    .param p2, "accuracy"    # I

    .prologue
    .line 105
    return-void
.end method

.method public onPause()V
    .locals 0

    .prologue
    .line 112
    invoke-direct {p0}, Lcom/miniclip/input/MCAccelerometer;->unregister()V

    .line 113
    return-void
.end method

.method public onResume()V
    .locals 1

    .prologue
    .line 126
    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->mHasWindowFocus:Z

    if-eqz v0, :cond_0

    .line 127
    const/4 v0, 0x0

    sput-boolean v0, Lcom/miniclip/input/MCAccelerometer;->mResumeOnFocus:Z

    .line 128
    invoke-direct {p0}, Lcom/miniclip/input/MCAccelerometer;->register()V

    .line 133
    :goto_0
    return-void

    .line 131
    :cond_0
    const/4 v0, 0x1

    sput-boolean v0, Lcom/miniclip/input/MCAccelerometer;->mResumeOnFocus:Z

    goto :goto_0
.end method

.method public onSensorChanged(Landroid/hardware/SensorEvent;)V
    .locals 7
    .param p1, "event"    # Landroid/hardware/SensorEvent;

    .prologue
    # 1. KIỂM TRA AN TOÀN (Giữ lại để không Crash)
    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z
    if-nez v0, :cond_go
    return-void

    :cond_go
    # 2. Chỉ xử lý nếu là Accelerometer (Type = 1)
    iget-object v0, p1, Landroid/hardware/SensorEvent;->sensor:Landroid/hardware/Sensor;
    invoke-virtual {v0}, Landroid/hardware/Sensor;->getType()I
    move-result v0
    const/4 v1, 0x1
    if-eq v0, v1, :cond_process
    return-void

    :cond_process
    # 3. LẤY MẢNG DỮ LIỆU
    iget-object v6, p1, Landroid/hardware/SensorEvent;->values:[F

    # 4. LOGIC MÀN HÌNH NGANG (LANDSCAPE)
    # Khi cầm ngang điện thoại:
    # - Nghiêng trái/phải là trục Y của sensor (values[1])
    # - Nghiêng trước/sau là trục X của sensor (values[0])

    # --- GAME X (Lái xe) = SENSOR Y ---
    const/4 v0, 0x1
    aget v0, v6, v0   # Lấy giá trị Y từ sensor

    # --- GAME Y (Cân bằng) = -SENSOR X (Đảo dấu trục X) ---
    const/4 v1, 0x0
    aget v1, v6, v1   # Lấy giá trị X từ sensor
    neg-float v1, v1  # Đảo dấu (thành số âm/dương ngược lại)

    # --- GAME Z = SENSOR Z (Giữ nguyên) ---
    const/4 v2, 0x2
    aget v2, v6, v2

    # --- TIMESTAMP ---
    iget-wide v3, p1, Landroid/hardware/SensorEvent;->timestamp:J

    # 5. GỌI HÀM C++ (NATIVE)
    # Hàm này nhận 4 tham số: float x, float y, float z, long timestamp
    invoke-static {v0, v1, v2, v3, v4}, Lcom/miniclip/input/MCAccelerometer;->onSensorChanged(FFFJ)V

    return-void
.end method

.method public onWindowFocusChanged(Z)V
    .locals 1
    .param p1, "hasWindowFocus"    # Z

    .prologue
    .line 117
    sput-boolean p1, Lcom/miniclip/input/MCAccelerometer;->mHasWindowFocus:Z

    .line 118
    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->mHasWindowFocus:Z

    if-eqz v0, :cond_0

    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->mResumeOnFocus:Z

    if-eqz v0, :cond_0

    .line 119
    const/4 v0, 0x0

    sput-boolean v0, Lcom/miniclip/input/MCAccelerometer;->mResumeOnFocus:Z

    .line 120
    invoke-direct {p0}, Lcom/miniclip/input/MCAccelerometer;->register()V

    .line 122
    :cond_0
    return-void
.end method
