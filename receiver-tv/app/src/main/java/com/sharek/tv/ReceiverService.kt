package com.sharek.tv

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.provider.Settings

/** Keeps Sharek discoverable and listening even when the app isn't open. */
class ReceiverService : Service() {
    private var discovery: Discovery? = null
    private var multicastLock: WifiManager.MulticastLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Hub.app = applicationContext
        goForeground()
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicastLock = wifi.createMulticastLock("sharek").apply { setReferenceCounted(false); acquire() }
        discovery = Discovery(deviceName(this)).also { it.start() }
        Hub.server = StreamServer(Hub).also { it.start() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        Hub.server?.shutdown(); Hub.server = null
        discovery?.shutdown(); discovery = null
        multicastLock?.release(); multicastLock = null
        super.onDestroy()
    }

    private fun goForeground() {
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "شارك", NotificationManager.IMPORTANCE_MIN)
            )
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(this)
        }
        val n = builder
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("شارك جاهز")
            .setContentText("بانتظار الاتصال")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, n)
        }
    }

    companion object {
        private const val CHANNEL = "sharek"

        fun start(context: Context) {
            val i = Intent(context, ReceiverService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
            } catch (_: Exception) {
            }
        }

        fun deviceName(c: Context): String {
            val n = if (Build.VERSION.SDK_INT >= 25) Settings.Global.getString(c.contentResolver, Settings.Global.DEVICE_NAME) else null
            return if (n.isNullOrBlank()) "${Build.MANUFACTURER} ${Build.MODEL}" else n
        }
    }
}

/** Starts the receiver after the TV boots and after Sharek is updated. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            ReceiverService.start(context)
        }
    }
}
