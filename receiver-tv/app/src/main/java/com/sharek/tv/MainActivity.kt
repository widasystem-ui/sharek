package com.sharek.tv

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.SurfaceTexture
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.KeyEvent
import android.view.Surface
import android.view.TextureView
import android.view.View
import android.view.WindowManager
import android.view.animation.LinearInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.net.Inet4Address
import java.net.NetworkInterface

class MainActivity : Activity(), TextureView.SurfaceTextureListener {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var root: FrameLayout
    private lateinit var video: TextureView
    private lateinit var idle: LinearLayout
    private lateinit var statusText: TextView
    private lateinit var statusDot: View
    private lateinit var info: TextView
    private lateinit var hint: TextView
    private var permissionScreen: View? = null
    private var approvalScreen: View? = null
    private var approvalCallback: ((Int) -> Unit)? = null
    private var countdown: Runnable? = null

    @Volatile var surface: Surface? = null
        private set
    @Volatile var isOnScreen = false
        private set
    private var streaming = false
    private var videoW = 1920
    private var videoH = 1080

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Hub.app = applicationContext
        ReceiverService.start(this)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        buildUi()
    }

    override fun onResume() {
        super.onResume()
        Hub.activity = this
        isOnScreen = true
        hideSystemUi()
        refreshPermissionScreen()
        if (!streaming) showWaiting(null)
    }

    override fun onPause() {
        isOnScreen = false
        super.onPause()
    }

    override fun onStop() {
        // Leaving the app (Home button): end any stream and decline a pending request.
        if (streaming) Hub.closeSession()
        approvalCallback?.let { answer(REJECT) }
        super.onStop()
    }

    override fun onDestroy() {
        if (Hub.activity === this) Hub.activity = null
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode == KeyEvent.KEYCODE_BACK && event.action == KeyEvent.ACTION_UP) {
            if (approvalScreen != null) { answer(REJECT); return true }
            if (permissionScreen != null) { later(); return true }
        }
        return super.dispatchKeyEvent(event)
    }

    // ------------------------------------------------------------------ layout

    private fun buildUi() {
        root = FrameLayout(this).apply {
            setBackgroundColor(Ui.INK)
            layoutDirection = View.LAYOUT_DIRECTION_RTL
        }

        // TextureView keeps the picture on the normal graphics layer, so the TV's video
        // processing (Motionflow, wide-mode zoom) never touches it.
        video = TextureView(this)
        video.surfaceTextureListener = this
        root.addView(video, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT, Gravity.CENTER))

        idle = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            background = Ui.background()
        }
        val logo = LogoView(this)
        idle.addView(logo, LinearLayout.LayoutParams(Ui.dpi(this, 112f), Ui.dpi(this, 112f)).apply {
            bottomMargin = Ui.dpi(this@MainActivity, 22f)
        })
        idle.addView(Ui.text(this, "شارك", 64f, Ui.TEXT, bold = true))
        idle.addView(Ui.text(this, "اعرض شاشتك على التلفزيون", 20f, Ui.MUTED).apply {
            setPadding(0, Ui.dpi(this@MainActivity, 2f), 0, Ui.dpi(this@MainActivity, 34f))
        })

        val pill = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = Ui.rounded(this@MainActivity, android.graphics.Color.parseColor("#14FFFFFF"), 40f,
                android.graphics.Color.parseColor("#22FFFFFF"), 1f)
            setPadding(Ui.dpi(this@MainActivity, 26f), Ui.dpi(this@MainActivity, 12f),
                Ui.dpi(this@MainActivity, 26f), Ui.dpi(this@MainActivity, 12f))
        }
        statusDot = View(this).apply {
            background = Ui.rounded(this@MainActivity, Ui.MINT, 10f)
        }
        pill.addView(statusDot, LinearLayout.LayoutParams(Ui.dpi(this, 12f), Ui.dpi(this, 12f)).apply {
            marginEnd = Ui.dpi(this@MainActivity, 14f)
        })
        statusText = Ui.text(this, "", 22f, Ui.TEXT)
        pill.addView(statusText)
        idle.addView(pill, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        ObjectAnimator.ofFloat(statusDot, View.ALPHA, 1f, 0.25f).apply {
            duration = 900
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            start()
        }

        info = Ui.text(this, "", 17f, Ui.MUTED).apply {
            setPadding(0, Ui.dpi(this@MainActivity, 28f), 0, 0)
        }
        idle.addView(info)
        hint = Ui.text(this, "", 15f, Ui.DIM).apply {
            setPadding(0, Ui.dpi(this@MainActivity, 8f), 0, 0)
        }
        idle.addView(hint)

        root.addView(idle, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        setContentView(root)
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> fitVideo() }
    }

    // ------------------------------------------------------------------ states (UI thread)

    fun showWaiting(reason: String?) {
        streaming = false
        idle.visibility = View.VISIBLE
        statusText.text = if (reason != null) "$reason — بانتظار الاتصال" else "بانتظار الاتصال…"
        statusDot.background = Ui.rounded(this, Ui.MINT, 10f)
        val name = ReceiverService.deviceName(this)
        info.text = "$name   •   ${localIp() ?: "غير متصل بالشبكة"}"
        hint.text = "افتح برنامج شارك على جهازك واختر «$name»"
    }

    fun showConnecting(peer: String) {
        streaming = true
        idle.visibility = View.VISIBLE
        statusText.text = "جاري الاتصال بـ $peer…"
        statusDot.background = Ui.rounded(this, android.graphics.Color.parseColor("#FFC857"), 10f)
    }

    fun showVideo() {
        streaming = true
        idle.visibility = View.GONE
    }

    fun setVideoSize(w: Int, h: Int) {
        videoW = w; videoH = h
        fitVideo()
    }

    // ------------------------------------------------------------------ connection request

    fun showApproval(peer: String, timeoutMs: Long, onAnswer: (Int) -> Unit) {
        dismissApproval()
        approvalCallback = onAnswer

        val scrim = FrameLayout(this).apply {
            clipChildren = false
            setBackgroundColor(Ui.SCRIM)
            isClickable = true
        }
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            background = Ui.rounded(this@MainActivity, Ui.CARD, 30f, Ui.CARD_STROKE, 1f)
            setPadding(Ui.dpi(this@MainActivity, 56f), Ui.dpi(this@MainActivity, 44f),
                Ui.dpi(this@MainActivity, 56f), Ui.dpi(this@MainActivity, 30f))
            clipChildren = false; clipToPadding = false
        }
        card.addView(LogoView(this), LinearLayout.LayoutParams(Ui.dpi(this, 64f), Ui.dpi(this, 64f)).apply {
            bottomMargin = Ui.dpi(this@MainActivity, 18f)
        })
        card.addWrap(Ui.text(this, "طلب عرض", 18f, Ui.MINT, bold = true))
        card.addWrap(Ui.text(this, "«$peer»", 40f, Ui.TEXT, bold = true).apply {
            setPadding(0, Ui.dpi(this@MainActivity, 6f), 0, Ui.dpi(this@MainActivity, 6f))
            maxLines = 1
        })
        card.addWrap(Ui.text(this, "يبي يعرض شاشته على هذا التلفزيون", 19f, Ui.MUTED).apply {
            setPadding(0, 0, 0, Ui.dpi(this@MainActivity, 34f))
        })

        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
            clipChildren = false; clipToPadding = false
            setPadding(0, Ui.dpi(this@MainActivity, 6f), 0, Ui.dpi(this@MainActivity, 6f))
        }
        val ok = Ui.button(this, "موافق", Ui.Kind.PRIMARY) { answer(ACCEPT) }
        val always = Ui.button(this, "السماح دائماً", Ui.Kind.SECONDARY) { answer(ALWAYS) }
        val no = Ui.button(this, "رفض", Ui.Kind.DANGER) { answer(REJECT) }
        val gap = Ui.dpi(this, 10f)
        for (b in listOf(ok, always, no)) {
            row.addView(b, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                marginStart = gap; marginEnd = gap
            })
        }
        card.addWrap(row)

        val left = Ui.text(this, "", 15f, Ui.DIM).apply {
            setPadding(0, Ui.dpi(this@MainActivity, 26f), 0, Ui.dpi(this@MainActivity, 10f))
        }
        card.addWrap(left)
        val track = FrameLayout(this).apply {
            background = Ui.rounded(this@MainActivity, android.graphics.Color.parseColor("#1FFFFFFF"), 3f)
        }
        val bar = View(this).apply {
            background = Ui.rounded(this@MainActivity, Ui.MINT, 3f)
            pivotX = 0f
        }
        track.addView(bar, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, Ui.dpi(this, 5f)))
        card.addView(track, LinearLayout.LayoutParams(Ui.dpi(this, 520f), Ui.dpi(this, 5f)))

        scrim.addView(card, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        root.addView(scrim, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        approvalScreen = scrim

        card.alpha = 0f
        card.scaleX = 0.94f; card.scaleY = 0.94f
        card.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(220).start()
        bar.post { bar.pivotX = if (bar.layoutDirection == View.LAYOUT_DIRECTION_RTL) bar.width.toFloat() else 0f }
        bar.animate().scaleX(0f).setDuration(timeoutMs).setInterpolator(LinearInterpolator()).start()

        val end = System.currentTimeMillis() + timeoutMs
        val tick = object : Runnable {
            override fun run() {
                val s = ((end - System.currentTimeMillis()) / 1000).coerceAtLeast(0)
                left.text = "يُرفض تلقائياً بعد $s ثانية"
                if (s > 0) ui.postDelayed(this, 1000)
            }
        }
        countdown = tick
        tick.run()
        ok.post { ok.requestFocus() }
    }

    fun dismissApproval() {
        countdown?.let { ui.removeCallbacks(it) }
        countdown = null
        approvalScreen?.let { root.removeView(it) }
        approvalScreen = null
        approvalCallback = null
    }

    private fun answer(result: Int) {
        val cb = approvalCallback
        dismissApproval()
        cb?.invoke(result)
    }

    // ------------------------------------------------------------------ first-run permission

    private fun prefs() = getSharedPreferences("sharek", Context.MODE_PRIVATE)

    private fun canOverlay() = Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(this)

    private fun refreshPermissionScreen() {
        val need = !canOverlay() && !prefs().getBoolean("overlay_later", false)
        if (need && permissionScreen == null) showPermissionScreen()
        if (!need && permissionScreen != null) {
            root.removeView(permissionScreen)
            permissionScreen = null
        }
    }

    private fun showPermissionScreen() {
        val screen = FrameLayout(this).apply { background = Ui.background(); isClickable = true }
        val col = LinearLayout(this).apply {
            clipChildren = false; clipToPadding = false
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            background = Ui.rounded(this@MainActivity, Ui.CARD, 30f, Ui.CARD_STROKE, 1f)
            setPadding(Ui.dpi(this@MainActivity, 60f), Ui.dpi(this@MainActivity, 46f),
                Ui.dpi(this@MainActivity, 60f), Ui.dpi(this@MainActivity, 44f))
        }
        col.addView(LogoView(this), LinearLayout.LayoutParams(Ui.dpi(this, 72f), Ui.dpi(this, 72f)).apply {
            bottomMargin = Ui.dpi(this@MainActivity, 20f)
        })
        col.addWrap(Ui.text(this, "خطوة وحدة قبل نبدأ", 34f, Ui.TEXT, bold = true))
        col.addWrap(Ui.text(this,
            "عشان يفتح شارك بنفسه أول ما يتصل فيه جهاز،\nيحتاج صلاحية «العرض فوق التطبيقات».",
            19f, Ui.MUTED).apply {
            setPadding(0, Ui.dpi(this@MainActivity, 14f), 0, Ui.dpi(this@MainActivity, 8f))
            setLineSpacing(0f, 1.25f)
        })
        col.addWrap(Ui.text(this, "في الصفحة الجاية اختر «شارك» وفعّلها، وبعدها ارجع.", 16f, Ui.DIM).apply {
            setPadding(0, 0, 0, Ui.dpi(this@MainActivity, 32f))
        })
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
            clipChildren = false; clipToPadding = false
            setPadding(0, Ui.dpi(this@MainActivity, 6f), 0, Ui.dpi(this@MainActivity, 6f))
        }
        val grant = Ui.button(this, "منح الصلاحية", Ui.Kind.PRIMARY) { openOverlaySettings() }
        val skip = Ui.button(this, "لاحقاً", Ui.Kind.SECONDARY) { later() }
        val gap = Ui.dpi(this, 10f)
        for (b in listOf(grant, skip)) {
            row.addView(b, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                marginStart = gap; marginEnd = gap
            })
        }
        col.addWrap(row)
        screen.addView(col, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        root.addView(screen, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        permissionScreen = screen
        grant.post { grant.requestFocus() }
    }

    private fun openOverlaySettings() {
        val withPkg = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
        try {
            startActivity(withPkg)
        } catch (_: Exception) {
            try { startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION)) } catch (_: Exception) {
                try { startActivity(Intent(Settings.ACTION_SETTINGS)) } catch (_: Exception) {}
            }
        }
    }

    private fun later() {
        prefs().edit().putBoolean("overlay_later", true).apply()
        refreshPermissionScreen()
    }

    // ------------------------------------------------------------------ surface

    override fun onSurfaceTextureAvailable(st: SurfaceTexture, width: Int, height: Int) { surface = Surface(st) }
    override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, width: Int, height: Int) {}
    override fun onSurfaceTextureDestroyed(st: SurfaceTexture): Boolean {
        surface?.release(); surface = null
        return true
    }
    override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}

    // ------------------------------------------------------------------ helpers

    @Suppress("DEPRECATION")
    private fun hideSystemUi() {
        window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_FULLSCREEN
            or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            or View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
    }

    /** Letterbox the video inside the screen while keeping its aspect ratio. */
    private fun fitVideo() {
        val rw = root.width
        val rh = root.height
        if (rw == 0 || rh == 0 || videoW == 0 || videoH == 0) return
        val scale = minOf(rw.toFloat() / videoW, rh.toFloat() / videoH)
        val w = (videoW * scale).toInt()
        val h = (videoH * scale).toInt()
        val lp = video.layoutParams as FrameLayout.LayoutParams
        if (lp.width != w || lp.height != h) {
            lp.width = w; lp.height = h; lp.gravity = Gravity.CENTER
            video.layoutParams = lp
        }
    }

    private fun localIp(): String? = try {
        NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.toList() }
            .firstOrNull { it is Inet4Address && !it.isLoopbackAddress }
            ?.hostAddress
    } catch (_: Exception) { null }

    private fun LinearLayout.addWrap(v: View) = addView(v, LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT))

    companion object {
        const val REJECT = 0
        const val ACCEPT = 1
        const val ALWAYS = 2
    }
}
