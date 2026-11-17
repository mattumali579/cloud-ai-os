package com.mattumali.visortracker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.BroadcastReceiver
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.net.ConnectivityManager
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONArray

/**
 * Free, on-device updater for the exact same Tracker package.
 *
 * GitHub Actions publishes a release named visor-tracker-v<versionCode>
 * containing a permanently signed asset named Visor-Fast-Tracking.apk.
 * Download directly from that public GitHub release over HTTPS, verify the
 * release digest (when available), verify APK package and version, then stream
 * into Android PackageInstaller. No developer token, USB, computer, root, or
 * new Android application/package is needed after the one-time signer migration.
 *
 * On Android 12+, self-updates MAY be silent when the system's conditions are
 * met. The system can always request user confirmation; never bypass it.
 */
object TrackerUpdater {
    const val PREF_AUTO_UPDATE = "auto_updates"
    private const val TAG = "VisorTrackerUpdates"
    private const val JOB_ID = 739092
    private const val CHANNEL = "visor_tracker_updates"
    private const val NOTIFICATION_ID = 7391
    private const val REPO_API =
        "https://api.github.com/repos/mattumali579/cloud-ai-os/releases?per_page=40"
    private const val ASSET_NAME = "Visor-Fast-Tracking.apk"
    private const val MAX_APK_BYTES = 180_000_000L
    private const val PERIOD = 12L * 60L * 60L * 1000L
    private val busy = AtomicBoolean(false)
    private val tag = Regex("^visor-tracker-v([1-9][0-9]*)$")

    fun enabled(context: Context): Boolean = context
        .getSharedPreferences(OverlayTrackingService.PREFS, Context.MODE_PRIVATE)
        .getBoolean(PREF_AUTO_UPDATE, true)

    fun configure(context: Context, enabled: Boolean) {
        context.getSharedPreferences(OverlayTrackingService.PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(PREF_AUTO_UPDATE, enabled).apply()
        schedule(context)
    }

    fun schedule(context: Context) {
        val scheduler = context.getSystemService(JobScheduler::class.java)
        if (!enabled(context)) {
            scheduler.cancel(JOB_ID)
            return
        }
        // Android controls exact timing and may defer work for battery or Wi-Fi.
        val info = JobInfo.Builder(
            JOB_ID, ComponentName(context, TrackerUpdateJob::class.java)
        )
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED)
            .setPeriodic(PERIOD)
            .build()
        scheduler.schedule(info)
    }

    fun checkInBackground(
        context: Context,
        allowMetered: Boolean = false,
        onStatus: (String) -> Unit = {}
    ) {
        if (!busy.compareAndSet(false, true)) return
        Thread {
            try {
                val network = context.getSystemService(ConnectivityManager::class.java)
                if (!allowMetered && network.isActiveNetworkMetered) {
                    onStatus("Automatic updates are waiting for unmetered Wi-Fi")
                } else {
                    installNewest(context.applicationContext, onStatus)
                }
            } catch (error: Exception) {
                Log.w(TAG, "Update check failed", error)
                onStatus("Update check failed: " + (error.message ?: "unknown"))
            } finally {
                busy.set(false)
            }
        }.apply { name = "visor-tracker-updates"; isDaemon = true }.start()
    }

    private data class Release(
        val version: Long,
        val url: String,
        val bytes: Long,
        val sha256: String?
    )

    private fun fetchRelease(): Release? {
        val connection = URL(REPO_API).openConnection() as HttpURLConnection
        connection.connectTimeout = 12_000
        connection.readTimeout = 20_000
        connection.setRequestProperty("User-Agent", "Visor-Fast-Tracking-Android-Updater")
        connection.setRequestProperty("Accept", "application/vnd.github+json")
        try {
            if (connection.responseCode != 200) {
                throw IllegalStateException("GitHub release API returned " + connection.responseCode)
            }
            val raw = connection.inputStream.bufferedReader().use { it.readText() }
            val releases = JSONArray(raw)
            var newest: Release? = null
            for (index in 0 until releases.length()) {
                val release = releases.optJSONObject(index) ?: continue
                if (release.optBoolean("draft") || release.optBoolean("prerelease")) continue
                val match = tag.matchEntire(release.optString("tag_name")) ?: continue
                val version = match.groupValues[1].toLongOrNull() ?: continue
                val assets = release.optJSONArray("assets") ?: continue
                for (assetIndex in 0 until assets.length()) {
                    val asset = assets.optJSONObject(assetIndex) ?: continue
                    if (asset.optString("name") != ASSET_NAME) continue
                    val bytes = asset.optLong("size", 0L)
                    if (bytes < 1_000_000L || bytes > MAX_APK_BYTES) continue
                    val url = asset.optString("browser_download_url")
                    val uri = Uri.parse(url)
                    if (uri.scheme != "https" || uri.host != "github.com") continue
                    val digest = asset.optString("digest")
                    val sha256 = if (digest.startsWith("sha256:")) {
                        digest.removePrefix("sha256:").lowercase()
                    } else null
                    if (newest == null || version > newest.version) {
                        newest = Release(version, url, bytes, sha256)
                    }
                }
            }
            return newest
        } finally { connection.disconnect() }
    }

    private fun currentVersion(context: Context): Long {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        return info.longVersionCode
    }

    fun runScheduled(context: Context) {
        if (!busy.compareAndSet(false, true)) return
        try {
            installNewest(context) { Log.i(TAG, it) }
        } catch (error: Exception) {
            Log.w(TAG, "Scheduled update failed", error)
        } finally {
            busy.set(false)
        }
    }

    private fun installNewest(context: Context, status: (String) -> Unit) {
        val release = fetchRelease()
        if (release == null || release.version <= currentVersion(context)) {
            status("Tracker is up to date")
            return
        }
        if (OverlayTrackingService.active) {
            status("Update available; will retry when tracking is stopped")
            return
        }
        if (Build.VERSION.SDK_INT >= 26 && !context.packageManager.canRequestPackageInstalls()) {
            notifySettings(context)
            status("One-time permission needed: Allow from this source")
            return
        }
        status("Downloading Tracker update " + release.version)
        val apk = File(context.cacheDir, "visor-self-update.apk")
        val connection = URL(release.url).openConnection() as HttpURLConnection
        connection.instanceFollowRedirects = true
        connection.connectTimeout = 20_000
        connection.readTimeout = 60_000
        val md = MessageDigest.getInstance("SHA-256")
        try {
            if (connection.responseCode !in 200..299) {
                throw IllegalStateException("APK download HTTP " + connection.responseCode)
            }
            var downloaded = 0L
            connection.inputStream.use { input ->
                apk.outputStream().use { output ->
                    val chunk = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(chunk)
                        if (n < 0) break
                        downloaded += n
                        if (downloaded > MAX_APK_BYTES) throw IllegalStateException("APK too large")
                        md.update(chunk, 0, n)
                        output.write(chunk, 0, n)
                    }
                }
            }
            if (downloaded != release.bytes) {
                throw IllegalStateException("APK size does not match published release")
            }
            val hash = md.digest().joinToString("") { "%02x".format(it) }
            if (release.sha256 != null && hash != release.sha256) {
                throw IllegalStateException("APK release checksum mismatch")
            }
            @Suppress("DEPRECATION")
            val info = context.packageManager.getPackageArchiveInfo(apk.absolutePath, 0)
                ?: throw IllegalStateException("APK cannot be parsed")
            if (info.packageName != context.packageName) {
                throw IllegalStateException("Update has a different package ID")
            }
            if (info.longVersionCode != release.version) {
                throw IllegalStateException("APK version does not match release tag")
            }
            status("Installing signed update " + release.version)
            val installer = context.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL
            ).apply {
                setAppPackageName(context.packageName)
                setSize(apk.length())
                if (Build.VERSION.SDK_INT >= 31) {
                    setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                }
            }
            val sessionId = installer.createSession(params)
            try {
                installer.openSession(sessionId).use { session ->
                    session.openWrite("base.apk", 0, apk.length()).use { output ->
                        apk.inputStream().use { it.copyTo(output) }
                        session.fsync(output)
                    }
                    val intent = Intent(context, TrackerUpdateReceiver::class.java).apply {
                        action = TrackerUpdateReceiver.ACTION_RESULT
                    }
                    // PackageInstaller populates a mutable callback intent with the result.
                    val pending = PendingIntent.getBroadcast(
                        context, sessionId, intent,
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
                    )
                    session.commit(pending.intentSender)
                }
            } catch (error: Exception) {
                installer.abandonSession(sessionId)
                throw error
            }
            status("Installer accepted update " + release.version)
        } finally {
            connection.disconnect()
            if (apk.exists()) apk.delete()
        }
    }

    private fun channel(context: Context): NotificationManager {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL, "Visor Tracker updates", NotificationManager.IMPORTANCE_DEFAULT
            )
        )
        return manager
    }

    fun notifySettings(context: Context) {
        val intent = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:" + context.packageName)
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        notifyAction(context, "Allow Tracker self-updates", "Enable Allow from this source once.", intent)
    }

    fun notifyAction(context: Context, title: String, detail: String, action: Intent) {
        val pending = PendingIntent.getActivity(
            context, 8302, action,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = Notification.Builder(context, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(title)
            .setContentText(detail)
            .setContentIntent(pending)
            .setAutoCancel(true)
            .build()
        channel(context).notify(NOTIFICATION_ID, notification)
    }
}

class TrackerUpdateJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        if (!TrackerUpdater.enabled(this)) return false
        // The updater owns its thread; we keep the job active for the network
        // and installation work, and let JobScheduler defer if the network fails.
        Thread {
            try {
                // A single check executes directly so jobFinished means done.
                TrackerUpdater.runScheduled(applicationContext)
            } finally {
                jobFinished(params, false)
            }
        }.apply { name = "visor-tracker-periodic-update"; isDaemon = true }.start()
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean = true
}

class TrackerUpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_RESULT) return
        val status = intent.getIntExtra(
            PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE
        )
        when (status) {
            PackageInstaller.STATUS_SUCCESS -> {
                Log.i("VisorTrackerUpdates", "Update installed")
            }
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirmation = if (Build.VERSION.SDK_INT >= 33) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirmation != null) {
                    confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    TrackerUpdater.notifyAction(
                        context, "Approve Visor Tracker update",
                        "Samsung requires one confirmation to install the new version.",
                        confirmation
                    )
                }
            }
            else -> {
                val reason = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                    ?: "Unknown installer failure"
                Log.e("VisorTrackerUpdates", "Install rejected: " + reason)
                TrackerUpdater.notifyAction(
                    context, "Visor Tracker update failed",
                    reason, Intent(context, MainActivity::class.java)
                )
            }
        }
    }

    companion object {
        const val ACTION_RESULT = "com.mattumali.visortracker.UPDATE_RESULT"
    }
}
