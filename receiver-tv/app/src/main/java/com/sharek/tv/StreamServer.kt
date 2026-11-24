package com.sharek.tv

import android.util.Log
import android.view.Surface
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer

interface SessionListener {
    /** Blocks until the person on the TV answers. [cancelled] turns true if the sender gives up. */
    fun requestApproval(name: String, id: String, cancelled: () -> Boolean): Boolean
    fun onConnected(peerName: String)
    fun onVideoSize(width: Int, height: Int)
    fun onFirstFrame()
    fun onDisconnected(reason: String)
    /** Blocks briefly until the display surface is ready; null if unavailable. */
    fun awaitSurface(): Surface?
}

/** Accepts senders; an approved sender replaces whoever was streaming before. */
class StreamServer(private val listener: SessionListener) : Thread("sharek-server") {
    @Volatile private var server: ServerSocket? = null
    @Volatile private var current: Session? = null
    private val lock = Any()

    override fun run() {
        try {
            val ss = ServerSocket().apply {
                reuseAddress = true
                bind(InetSocketAddress(Protocol.STREAM_PORT))
            }
            server = ss
            while (!isInterrupted) {
                val sock = ss.accept()
                Session(sock).start()
            }
        } catch (e: Exception) {
            if (!isInterrupted) Log.w(TAG, "server stopped", e)
        }
    }

    fun shutdown() {
        interrupt()
        try { server?.close() } catch (_: Exception) {}
        current?.close()
    }

    fun closeCurrent() { current?.close() }

    private fun activate(s: Session) {
        val old = synchronized(lock) { val o = current; current = s; o }
        if (old != null && old !== s) old.close()
    }

    private inner class Session(private val sock: Socket) : Thread("sharek-session") {
        private var video: VideoDecoder? = null
        private var audio: AudioPlayer? = null

        fun close() {
            try { sock.close() } catch (_: Exception) {}
        }

        override fun run() {
            var reason = "انقطع الاتصال"
            var active = false
            try {
                sock.tcpNoDelay = true
                sock.receiveBufferSize = 1024 * 1024
                val input = DataInputStream(BufferedInputStream(sock.getInputStream(), 256 * 1024))
                val out = sock.getOutputStream()

                // --- handshake
                sock.soTimeout = 10_000
                val magic = ByteArray(4)
                input.readFully(magic)
                if (String(magic, Charsets.US_ASCII) != Protocol.MAGIC) throw IllegalStateException("bad magic")
                val ver = input.readUnsignedByte()
                if (ver != Protocol.VERSION) throw IllegalStateException("version $ver unsupported")
                val type = input.readUnsignedByte()
                val len = input.readInt()
                if (type != Protocol.T_HELLO || len !in 0..4096) throw IllegalStateException("expected hello")
                val hello = ByteArray(len).also { input.readFully(it) }
                val json = try { JSONObject(String(hello, Charsets.UTF_8)) } catch (_: Exception) { JSONObject() }
                val name = json.optString("name").ifBlank { sock.inetAddress.hostAddress ?: "جهاز" }
                val id = json.optString("id")

                // --- ask the person on the TV
                sock.soTimeout = 150
                val approved = listener.requestApproval(name, id) { senderGone(input) }
                if (!approved) {
                    try { out.write(Protocol.REJECTED); out.flush() } catch (_: Exception) {}
                    return
                }
                out.write(Protocol.APPROVED)
                out.flush()
                sock.soTimeout = 0

                activate(this)
                active = true
                val surface = listener.awaitSurface() ?: throw IllegalStateException("no surface")
                val dec = VideoDecoder(surface, { listener.onFirstFrame() }, { w, h -> listener.onVideoSize(w, h) })
                video = dec
                listener.onConnected(name)

                while (true) {
                    val t = input.readUnsignedByte()
                    val n = input.readInt()
                    if (n < 0 || n > Protocol.MAX_PACKET) throw IllegalStateException("bad length $n")
                    val buf = ByteArray(n)
                    input.readFully(buf)
                    when (t) {
                        Protocol.T_VIDEO -> if (n > 8) {
                            val pts = ByteBuffer.wrap(buf, 0, 8).long
                            dec.feed(buf, 8, n - 8, pts)
                        }
                        Protocol.T_AUDIO -> audio?.push(buf)
                        Protocol.T_CONFIG -> {
                            val bb = ByteBuffer.wrap(buf)
                            val w = bb.short.toInt() and 0xFFFF
                            val h = bb.short.toInt() and 0xFFFF
                            bb.get() // fps (informational)
                            val rate = bb.int
                            val ch = bb.get().toInt()
                            dec.setSize(w, h)
                            listener.onVideoSize(w, h)
                            audio?.release(); audio = null
                            if (rate > 0 && ch > 0) audio = AudioPlayer(rate, ch)
                        }
                        Protocol.T_BYE -> { reason = "تم إيقاف المشاركة"; break }
                        else -> {}
                    }
                }
            } catch (e: Exception) {
                Log.i(TAG, "session ended: ${e.message}")
            } finally {
                video?.release()
                audio?.release()
                close()
                if (active) {
                    val stillCurrent = synchronized(lock) {
                        if (current === this) { current = null; true } else false
                    }
                    // A replaced session must not overwrite the new session's screen.
                    if (stillCurrent) listener.onDisconnected(reason)
                }
            }
        }

        /** True once the sender has closed the connection (cancelled while we were asking). */
        private fun senderGone(input: DataInputStream): Boolean = try {
            input.mark(1)
            val b = input.read()
            if (b >= 0) input.reset()
            b < 0
        } catch (_: SocketTimeoutException) {
            false
        } catch (_: Exception) {
            true
        }
    }

    companion object { private const val TAG = "SharekServer" }
}
