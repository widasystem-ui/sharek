package com.sharek.tv

import android.util.Log
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress

/** Answers laptop broadcasts so the sender can find this TV automatically. */
class Discovery(private val name: String) : Thread("sharek-discovery") {
    @Volatile private var socket: DatagramSocket? = null

    override fun run() {
        try {
            val s = DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                bind(InetSocketAddress(Protocol.DISCOVERY_PORT))
            }
            socket = s
            val buf = ByteArray(512)
            val reply = "SHAREK_HERE|$name|${Protocol.STREAM_PORT}".toByteArray(Charsets.UTF_8)
            while (!isInterrupted) {
                val p = DatagramPacket(buf, buf.size)
                s.receive(p)
                val msg = String(p.data, 0, p.length, Charsets.UTF_8)
                if (msg.startsWith("SHAREK_DISCOVER")) {
                    s.send(DatagramPacket(reply, reply.size, p.socketAddress))
                }
            }
        } catch (e: Exception) {
            if (!isInterrupted) Log.w("SharekDiscovery", "stopped", e)
        }
    }

    fun shutdown() {
        interrupt()
        socket?.close()
    }
}
