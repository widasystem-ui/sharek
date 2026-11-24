package com.sharek.tv

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/** Plays raw PCM s16le and keeps latency bounded by dropping when it backs up. */
class AudioPlayer(sampleRate: Int, channels: Int) {
    private val bytesPerMs = sampleRate * channels * 2 / 1000
    private val track: AudioTrack
    private val queue = ArrayBlockingQueue<ByteArray>(128)
    @Volatile private var running = true
    private var queuedBytes = 0L
    private val thread: Thread

    init {
        val chMask = if (channels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
        val minBuf = AudioTrack.getMinBufferSize(sampleRate, chMask, AudioFormat.ENCODING_PCM_16BIT)
        val bufSize = maxOf(minBuf, bytesPerMs * 60)
        track = AudioTrack(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE)
                .build(),
            AudioFormat.Builder()
                .setSampleRate(sampleRate)
                .setChannelMask(chMask)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .build(),
            bufSize, AudioTrack.MODE_STREAM, AudioManager.AUDIO_SESSION_ID_GENERATE
        )
        track.play()
        thread = Thread({ loop() }, "sharek-audio").apply { start() }
    }

    fun push(pcm: ByteArray) {
        synchronized(this) {
            // More than ~150 ms waiting? Drop the backlog to stay in sync with video.
            if (queuedBytes > bytesPerMs * 150) {
                queue.clear(); queuedBytes = 0
            }
            if (queue.offer(pcm)) queuedBytes += pcm.size
        }
    }

    private fun loop() {
        while (running) {
            val b = try {
                queue.poll(200, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                null
            } ?: continue
            synchronized(this) { queuedBytes -= b.size }
            track.write(b, 0, b.size)
        }
    }

    fun release() {
        running = false
        thread.interrupt()
        thread.join(300)
        try { track.stop() } catch (_: Exception) {}
        track.release()
    }
}
