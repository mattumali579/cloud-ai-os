package com.mattumali.visortracker

/**
 * How a signed Tracker APK is found. GitHub's release API is the fast path.
 * Direct download URLs are the fallback when that API stalls.
 */
object TrackerRelease {
    const val ASSET_NAME = "Visor-Fast-Tracking.apk"
    const val RELEASES_PAGE = "https://github.com/mattumali579/cloud-ai-os/releases"
    const val DOWNLOAD_ROOT =
        "https://github.com/mattumali579/cloud-ai-os/releases/download"
    private val tag = Regex("\"tag_name\"\\s*:\\s*\"visor-tracker-v([1-9][0-9]*)\"")

    data class Found(val version: Long, val url: String)

    fun downloadUrl(version: Long): String =
        "$DOWNLOAD_ROOT/visor-tracker-v$version/$ASSET_NAME"

    fun fallbackVersions(installed: Long, lookahead: Int = 3): List<Long> {
        if (installed < 1L) return emptyList()
        return (1..lookahead).map { installed + it }
    }

    fun newestFromApiJson(raw: String): Found? {
        var newest: Found? = null
        for (match in tag.findAll(raw)) {
            val version = match.groupValues[1].toLongOrNull() ?: continue
            if (newest == null || version > newest.version) {
                newest = Found(version, downloadUrl(version))
            }
        }
        return newest
    }
}
