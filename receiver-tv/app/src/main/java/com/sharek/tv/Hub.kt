package com.sharek.tv

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Connects the always-running network side (ReceiverService) with the screen (MainActivity).
 * When a sender connects and the app isn't on screen, it brings MainActivity up
 * (allowed because the app holds the "display over other apps" permission).
 */
object Hub : SessionListener {
    private const val TAG = "SharekHub"
    lateinit var app: Context
    @Volatile var activity: MainActivity? = null
    @Volatile var server: StreamServer? = null
    private val ui = Handler(Looper.getMainLooper())

    private fun prefs() = app.getSharedPreferences("sharek", Context.MODE_PRIVATE)

    private fun isTrusted(id: String) =
        id.isNotEmpty() && prefs().getStringSet("trusted", emptySet())!!.contains(id)

    private fun trust(id: String) {
        if (id.isEmpty()) return
        val set = HashSet(prefs().getStringSet("trusted", emptySet())!!)
        set.add(id)
        prefs().edit().putStringSet("trusted", set).apply()
    }

    fun closeSession() { server?.closeCurrent() }

    /** Makes sure MainActivity is on screen, launching it if needed. */
    private fun ensureActivity(): MainActivity? {
        activity?.let { if (it.isOnScreen) return it }
        try {
            app.startActivity(
                Intent(app, MainActivity::class.java).addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP
                )
            )
        } catch (e: Exception) {
            Log.w(TAG, "cannot open the app from the background", e)
        }
        repeat(100) {
            activity?.let { if (it.isOnScreen) return it }
            Thread.sleep(100)
        }
        return null
    }

    // ---- SessionListener (network threads) ----

    @Synchronized
    override fun requestApproval(name: String, id: String, cancelled: () -> Boolean): Boolean {
        if (isTrusted(id)) return true
        val a = ensureActivity() ?: return false
        val latch = CountDownLatch(1)
        val answer = java.util.concurrent.atomic.AtomicInteger(MainActivity.REJECT)
        ui.post {
            a.showApproval(name, Protocol.APPROVAL_TIMEOUT_MS) { r ->
                answer.set(r)
                latch.countDown()
            }
        }
        val deadline = SystemClock.elapsedRealtime() + Protocol.APPROVAL_TIMEOUT_MS
        while (!latch.await(300, TimeUnit.MILLISECONDS)) {
            if (cancelled() || SystemClock.elapsedRealtime() > deadline) {
                ui.post { a.dismissApproval() }
                return false
            }
        }
        if (answer.get() == MainActivity.ALWAYS) trust(id)
        return answer.get() != MainActivity.REJECT
    }

    override fun awaitSurface(): Surface? {
        ensureActivity()
        repeat(80) {
            activity?.surface?.let { if (it.isValid) return it }
            Thread.sleep(100)
        }
        return null
    }

    override fun onConnected(peerName: String) {
        ui.post { activity?.showConnecting(peerName) }
    }

    override fun onVideoSize(width: Int, height: Int) {
        ui.post { activity?.setVideoSize(width, height) }
    }

    override fun onFirstFrame() {
        ui.post { activity?.showVideo() }
    }

    override fun onDisconnected(reason: String) {
        ui.post { activity?.showWaiting(reason) }
    }
}
