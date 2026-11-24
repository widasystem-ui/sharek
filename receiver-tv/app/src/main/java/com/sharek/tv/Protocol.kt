package com.sharek.tv

/**
 * Sharek wire protocol, all integers big-endian.
 *
 * Discovery (UDP [DISCOVERY_PORT]):
 *   sender broadcasts  "SHAREK_DISCOVER"
 *   TV replies         "SHAREK_HERE|<tv name>|<tcp port>"
 *
 * Stream (TCP [STREAM_PORT]):
 *   sender -> TV: "SHRK" + version(u8)
 *   sender -> TV: first packet must be T_HELLO with JSON {"name": "...", "id": "..."}
 *   TV -> sender: one byte, [APPROVED] or [REJECTED] (after the person answers on the TV)
 *   sender -> TV: packets type(u8) + length(u32) + payload
 */
object Protocol {
    const val DISCOVERY_PORT = 47800
    const val STREAM_PORT = 47801
    const val VERSION = 2
    const val MAGIC = "SHRK"

    const val T_PING = 0
    const val T_VIDEO = 1   // pts_us(u64) + H.264 Annex-B access unit (SPS/PPS before every IDR)
    const val T_AUDIO = 2   // PCM s16le interleaved
    const val T_CONFIG = 3  // width(u16) height(u16) fps(u8) sampleRate(u32) channels(u8)
    const val T_BYE = 4
    const val T_HELLO = 5   // UTF-8 JSON {"name", "id"}

    const val APPROVED = 1
    const val REJECTED = 0

    const val APPROVAL_TIMEOUT_MS = 60_000L
    const val MAX_PACKET = 16 * 1024 * 1024
}
