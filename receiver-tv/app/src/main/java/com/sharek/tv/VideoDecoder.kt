package com.sharek.tv

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.util.Log
import android.view.Surface
import java.nio.ByteBuffer

/** Low-latency H.264 decoder that renders straight to a Surface. */
class VideoDecoder(
    private val surface: Surface,
    private val onFirstFrame: () -> Unit,
    private val onSize: (Int, Int) -> Unit,
) {
    private var codec: MediaCodec? = null
    private var drainThread: Thread? = null
    @Volatile private var running = false
    private var width = 1920
    private var height = 1080
    @Volatile private var firstFrameSent = false
    private val queuedAt = java.util.concurrent.ConcurrentHashMap<Long, Long>()

    fun setSize(w: Int, h: Int) {
        if (w == width && h == height) return
        width = w; height = h
        // New resolution: restart on the next keyframe.
        release()
    }

    /** Feed one access unit. Frames before the first SPS/PPS are dropped. */
    fun feed(data: ByteArray, offset: Int, length: Int, ptsUs: Long) {
        if (codec == null) {
            val csd = findParameterSets(data, offset, length) ?: return
            try {
                start(csd.first, csd.second)
            } catch (e: Exception) {
                Log.e(TAG, "cannot start decoder", e)
                release()
                return
            }
        }
        val c = codec ?: return
        try {
            val idx = c.dequeueInputBuffer(100_000)
            if (idx < 0) {
                Log.w(TAG, "decoder busy, dropping frame")
                return
            }
            val buf = c.getInputBuffer(idx)!!
            buf.clear()
            if (length > buf.remaining()) {
                Log.w(TAG, "frame too large ($length), dropping")
                c.queueInputBuffer(idx, 0, 0, ptsUs, 0)
                return
            }
            buf.put(data, offset, length)
            queuedAt[ptsUs] = System.nanoTime()
            c.queueInputBuffer(idx, 0, length, ptsUs, 0)
        } catch (e: IllegalStateException) {
            Log.e(TAG, "decoder error, restarting", e)
            release()
        }
    }

    private fun baseFormat(sps: ByteArray, pps: ByteArray): MediaFormat {
        val f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height)
        f.setByteBuffer("csd-0", ByteBuffer.wrap(sps))
        f.setByteBuffer("csd-1", ByteBuffer.wrap(pps))
        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, maxOf(width * height, 2 * 1024 * 1024))
        return f
    }

    private fun start(sps: ByteArray, pps: ByteArray) {
        val format = baseFormat(sps, pps)
        if (Build.VERSION.SDK_INT >= 23) format.setInteger(MediaFormat.KEY_PRIORITY, 0)
        if (Build.VERSION.SDK_INT >= 30) format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
        // Vendor low-latency hints (ignored where unsupported).
        format.setInteger("vendor.low-latency.enable", 1)
        format.setInteger("vendor.qti-ext-dec-low-latency.enable", 1)
        format.setInteger("vendor.rtc-ext-dec-low-latency.enable", 1)

        var c = createCodec()
        try {
            c.configure(format, surface, null, 0)
        } catch (e: Exception) {
            // Some decoders reject unknown keys; retry with a plain format.
            Log.w(TAG, "configure with hints failed, retrying plain", e)
            c.release()
            c = createCodec()
            c.configure(baseFormat(sps, pps), surface, null, 0)
        }
        c.start()
        codec = c
        running = true
        firstFrameSent = false
        drainThread = Thread({ drain(c) }, "sharek-drain").apply { start() }
        Log.i(TAG, "decoder started ${c.name} ${width}x$height")
    }

    /** Debug override: `adb shell setprop debug.sharek.decoder <codec name>`. */
    private fun createCodec(): MediaCodec {
        val forced = try {
            Class.forName("android.os.SystemProperties")
                .getMethod("get", String::class.java).invoke(null, "debug.sharek.decoder") as String
        } catch (_: Exception) { "" }
        if (forced.isNotBlank()) {
            try { return MediaCodec.createByCodecName(forced) } catch (e: Exception) { Log.w(TAG, "no codec $forced", e) }
        }
        return MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
    }

    private fun drain(c: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        var frames = 0
        var decodeMsSum = 0L
        var windowStart = System.nanoTime()
        while (running) {
            try {
                val idx = c.dequeueOutputBuffer(info, 20_000)
                if (idx >= 0) {
                    c.releaseOutputBuffer(idx, true)
                    if (!firstFrameSent) { firstFrameSent = true; onFirstFrame() }
                    frames++
                    queuedAt.remove(info.presentationTimeUs)?.let { decodeMsSum += (System.nanoTime() - it) / 1_000_000 }
                    val now = System.nanoTime()
                    if (now - windowStart >= 5_000_000_000L) {
                        val secs = (now - windowStart) / 1e9
                        Log.i(TAG, "rendered %.1f fps, decode %.1f ms".format(frames / secs, decodeMsSum.toDouble() / maxOf(frames, 1)))
                        frames = 0; decodeMsSum = 0; windowStart = now
                        if (queuedAt.size > 200) queuedAt.clear()
                    }
                } else if (idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    val f = c.outputFormat
                    var w = f.getInteger(MediaFormat.KEY_WIDTH)
                    var h = f.getInteger(MediaFormat.KEY_HEIGHT)
                    if (f.containsKey("crop-right") && f.containsKey("crop-left")) {
                        w = f.getInteger("crop-right") - f.getInteger("crop-left") + 1
                    }
                    if (f.containsKey("crop-bottom") && f.containsKey("crop-top")) {
                        h = f.getInteger("crop-bottom") - f.getInteger("crop-top") + 1
                    }
                    if (w > 0 && h > 0) onSize(w, h)
                }
            } catch (e: IllegalStateException) {
                break
            }
        }
    }

    fun release() {
        running = false
        drainThread?.join(500)
        drainThread = null
        codec?.let {
            try { it.stop() } catch (_: Exception) {}
            try { it.release() } catch (_: Exception) {}
        }
        codec = null
    }

    companion object {
        private const val TAG = "SharekVideo"

        /** Returns (SPS, PPS) with 4-byte start codes, or null if the AU has neither. */
        fun findParameterSets(d: ByteArray, off: Int, len: Int): Pair<ByteArray, ByteArray>? {
            val end = off + len
            // (payloadStart, startCodeStart) for every NAL unit.
            val starts = ArrayList<IntArray>()
            var i = off
            while (i + 3 <= end) {
                if (d[i].toInt() == 0 && d[i + 1].toInt() == 0 && d[i + 2].toInt() == 1) {
                    val scStart = if (i > off && d[i - 1].toInt() == 0) i - 1 else i
                    starts.add(intArrayOf(i + 3, scStart))
                    i += 3
                } else {
                    i++
                }
            }
            var sps: ByteArray? = null
            var pps: ByteArray? = null
            for (k in starts.indices) {
                val s = starts[k][0]
                val e = if (k + 1 < starts.size) starts[k + 1][1] else end
                if (s >= e) continue
                val type = d[s].toInt() and 0x1F
                if ((type == 7 && sps == null) || (type == 8 && pps == null)) {
                    val nal = ByteArray(4 + (e - s))
                    nal[3] = 1
                    System.arraycopy(d, s, nal, 4, e - s)
                    if (type == 7) sps = nal else pps = nal
                }
            }
            val a = sps ?: return null
            val b = pps ?: return null
            return Pair(a, b)
        }
    }
}
