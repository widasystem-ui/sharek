<div dir="rtl">

# شارك (Sharek)

اعرض شاشة الكمبيوتر وصوته على تلفزيون أندرويد / Google TV (مثل Sony Bravia) عبر الواي فاي. **مجاني، بدون إعلانات، وبدون انقطاع.**

## التحميل

من صفحة [الإصدارات](../../releases/latest):

| الجهاز | الملف |
|---|---|
| الكمبيوتر (ويندوز 10/11) | `Sharek-Windows.zip`: فكّه وشغّل `Sharek.exe` |
| التلفزيون (Android TV / Google TV) | `Sharek-TV.apk` |

> ويندوز بيطلع تحذير SmartScreen لأن البرنامج بدون توقيع رقمي. اضغط **مزيد من المعلومات** وبعدها **تشغيل على أي حال**.

## المميزات

- ٦٠ إطار بالثانية، والضغط يتم على كرت الشاشة (Intel QSV / NVIDIA NVENC / AMD AMF).
- تختار من وين يطلع الصوت: التلفزيون، أو الكمبيوتر، أو الاثنين، وتقدر تغيّره وأنت تعرض.
- وضع **التلفزيون فقط**: شاشة الكمبيوتر تنطفي والعرض يكمل على التلفزيون، وترجع الشاشة لما يوقف العرض (<kbd>Ctrl+Alt+D</kbd>).
- التلفزيون يعرض **«طلب عرض من (اسم الجهاز)»**، وتختار: موافق، أو السماح دائماً، أو رفض.
- تطبيق التلفزيون يفتح بنفسه أول ما يتصل فيه جهاز.
- التصغير يودّي البرنامج جنب الساعة (مو التاسك بار)، والاختصار <kbd>Ctrl+Alt+S</kbd> يبدأ ويوقف المشاركة من أي مكان.
- شكل الصورة: ملء الشاشة أو بحواف سوداء.

## تثبيت التطبيق على التلفزيون

**الطريقة الأسهل (بدون كمبيوتر):**
1. ثبّت تطبيق **Downloader** من متجر Google Play في التلفزيون.
2. اكتب فيه هذا الرابط:
   `https://github.com/widasystem-ui/sharek/releases/latest/download/Sharek-TV.apk`
3. اسمح له بتثبيت التطبيقات لما يطلب، وثبّت شارك.

**أو بفلاشة USB:** انسخ ملف الـ APK على فلاشة، وافتحه من مدير الملفات في التلفزيون.

**أو عبر ADB من الكمبيوتر:** فعّل «تصحيح أخطاء USB» من خيارات المطور في التلفزيون، وبعدها شغّل:
```powershell
.\install-tv.ps1 <IP التلفزيون>
```

أول مرة تفتح فيها شارك على التلفزيون بيطلب صلاحية **«العرض فوق التطبيقات»**. فعّلها عشان يفتح بنفسه لما تتصل.

## الاستخدام

1. افتح شارك على الكمبيوتر، والتلفزيون بيطلع في القائمة تلقائياً.
2. اضغط **ابدأ المشاركة**.
3. اضغط **موافق** على التلفزيون بالريموت.

</div>

---

# Sharek

Mirror your Windows screen and sound to an Android TV / Google TV (tested on a Sony BRAVIA) over Wi-Fi. Free, no ads, no time limits.

**Download** the Windows app (`Sharek-Windows.zip`) and the TV app (`Sharek-TV.apk`) from [Releases](../../releases/latest).

### Features
- 60 fps, hardware H.264 encoding (Intel QSV / NVIDIA NVENC / AMD AMF, with Media Foundation and x264 as fallbacks).
- Choose where sound plays: TV, computer, or both (can be switched while sharing).
- **TV only** mode: the laptop screen goes dark (backlight 0 + a cover window that capture skips) while the TV keeps showing the desktop; brightness comes back when sharing stops. <kbd>Ctrl+Alt+D</kbd> toggles it.
- The TV asks before anyone can share: **Accept / Always allow / Reject**.
- The TV app opens by itself when a device connects (it asks for the "display over other apps" permission on first run).
- Minimises to the tray (not the taskbar); global shortcut <kbd>Ctrl+Alt+S</kbd>. Sharek's own window never shows on the TV.

### How it works
| Step | Details |
|---|---|
| Capture | DXGI Desktop Duplication (`ddagrab`) through PyAV, scaled to 1080p |
| Encode | Low-latency hardware H.264, no B-frames, SPS/PPS before every keyframe |
| Audio | WASAPI loopback, PCM 48 kHz stereo |
| Discovery | UDP broadcast on port 47800 |
| Stream | One TCP connection on port 47801: handshake, approval byte from the TV, then typed packets |
| TV | `MediaCodec` hardware decoding into a `TextureView` (this bypasses the TV's motion processing), `AudioTrack` for sound |

### Build from source
```powershell
# Windows app
cd sender-windows
pip install -r requirements.txt
python app.py                      # run the window
python sharek.py --list            # find TVs from the command line
python sharek.py --tv 192.168.1.50 # share without the window

# TV app (JDK 17+, Android SDK)
cd receiver-tv
.\gradlew.bat assembleRelease
```

### Project layout
```
sender-windows/   Windows app: sharek.py (engine + CLI), app.py (window, tray, shortcut), ui/index.html
receiver-tv/      Android TV app (Kotlin, no external libraries)
install-tv.ps1    Installs the APK on a TV over ADB
```

License: [MIT](LICENSE)
