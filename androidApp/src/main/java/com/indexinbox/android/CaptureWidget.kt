package com.indexinbox.android

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.appwidget.AppWidgetProvider
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Color
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.widget.RemoteViews
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import retrofit2.HttpException
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit

internal object CaptureWidgetState {
    private const val PREFS = "capture_widget_state"
    private const val STATUS = "status"
    private const val DETAIL = "detail"
    private const val STARTED_AT = "started_at"
    private const val FILE = "file"

    fun status(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(STATUS, "ready") ?: "ready"
    fun detail(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(DETAIL, "") ?: ""
    fun startedAt(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getLong(STARTED_AT, 0L)
    fun file(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(FILE, null)

    fun set(context: Context, status: String, detail: String = "", file: String? = null) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(STATUS, status)
            .putString(DETAIL, detail)
            .putLong(STARTED_AT, if (status == "recording") System.currentTimeMillis() else 0L)
            .apply {
                if (file == null) remove(FILE) else putString(FILE, file)
            }
            .apply()
        CaptureWidgetProvider.updateAll(context)
    }
}

class CaptureWidgetProvider : AppWidgetProvider() {
    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action == Intent.ACTION_CONFIGURATION_CHANGED) updateAll(context)
    }

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        ids.forEach { manager.updateAppWidget(it, views(context)) }
    }

    companion object {
        fun updateAll(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val component = ComponentName(context, CaptureWidgetProvider::class.java)
            manager.updateAppWidget(component, views(context))
        }

        private fun views(context: Context): RemoteViews {
            val auth = AuthStore(context)
            val status = CaptureWidgetState.status(context)
            val dark = widgetUsesDarkTheme(auth.themeMode, context.resources.configuration.uiMode)
            val category = auth.widgetCaptureCategory.replaceFirstChar(Char::uppercase)
            val categoryWithArticle = if (auth.widgetCaptureCategory == "idea") "an $category" else "a $category"
            val hasMic = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            val ready = auth.token != null && hasMic
            val (title, detail) = when {
                !ready -> "Set up Index Inbox" to if (auth.token == null) "Tap to sign in" else "Tap to enable microphone"
                status == "recording" -> "Recording…" to "Tap to stop"
                status == "uploading" -> "Uploading…" to "Your Item is being saved"
                status == "saved" -> "Saved" to "Tap to record another $category"
                status == "queued" -> "Saved offline" to "Upload will retry automatically"
                status == "error" -> "Recording not sent" to CaptureWidgetState.detail(context).ifBlank { "Tap to open Index Inbox" }
                else -> "Index Inbox" to "Tap to record $categoryWithArticle"
            }
            val intent = when {
                !ready || status == "error" -> Intent(context, MainActivity::class.java).apply {
                    action = MainActivity.ACTION_WIDGET_SETUP
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                }
                status == "recording" && auth.widgetCaptureMode == "review" ->
                    Intent(context, MainActivity::class.java).apply {
                        action = MainActivity.ACTION_WIDGET_REVIEW
                        flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                    }
                status == "recording" -> Intent(context, AudioCaptureService::class.java).setAction(AudioCaptureService.ACTION_STOP)
                else -> Intent(context, AudioCaptureService::class.java).setAction(AudioCaptureService.ACTION_START)
            }
            val pendingIntent = if (intent.component?.className == MainActivity::class.java.name) {
                PendingIntent.getActivity(context, 301, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            } else {
                PendingIntent.getForegroundService(context, 302, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            }
            return RemoteViews(context.packageName, R.layout.capture_widget).apply {
                setInt(
                    R.id.widget_root,
                    "setBackgroundResource",
                    if (dark) R.drawable.widget_background_dark else R.drawable.widget_background,
                )
                setTextViewText(R.id.widget_title, title)
                setTextViewText(R.id.widget_status, detail)
                setTextColor(R.id.widget_title, if (dark) Color.rgb(247, 245, 239) else Color.rgb(23, 25, 30))
                setTextColor(R.id.widget_status, if (dark) Color.rgb(185, 180, 170) else Color.rgb(91, 93, 99))
                setInt(
                    R.id.widget_icon,
                    "setColorFilter",
                    when {
                        status == "recording" -> Color.rgb(224, 84, 76)
                        dark -> Color.rgb(255, 202, 72)
                        else -> Color.rgb(49, 92, 73)
                    },
                )
                setOnClickPendingIntent(R.id.widget_root, pendingIntent)
            }
        }
    }
}

internal fun widgetUsesDarkTheme(themeMode: String, uiMode: Int): Boolean = when (themeMode) {
    "dark" -> true
    "light" -> false
    else -> uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
}

class AudioCaptureService : Service() {
    private var recorder: MediaRecorder? = null
    private var output: File? = null
    private var startedElapsed = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val maximumDuration = Runnable { finishRecording(upload = true) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startRecording()
            ACTION_STOP -> finishRecording(upload = true)
            ACTION_CANCEL -> finishRecording(upload = false)
        }
        return START_NOT_STICKY
    }

    private fun startRecording() {
        if (recorder != null) return
        val auth = AuthStore(this)
        if (auth.token == null || ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            CaptureWidgetState.set(this, "error", "Open the app to finish setup")
            stopSelf()
            return
        }
        createChannel()
        startForeground(NOTIFICATION_ID, recordingNotification())
        val directory = File(filesDir, "widget-audio").apply { mkdirs() }
        val file = File(directory, "${UUID.randomUUID()}.m4a")
        try {
            @Suppress("DEPRECATION")
            recorder = MediaRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioEncodingBitRate(96_000)
                setAudioSamplingRate(44_100)
                setOutputFile(file.absolutePath)
                prepare()
                start()
            }
            output = file
            active = this
            startedElapsed = SystemClock.elapsedRealtime()
            CaptureWidgetState.set(this, "recording", file = file.absolutePath)
            handler.postDelayed(maximumDuration,TimeUnit.SECONDS.toMillis(auth.widgetRecordingSeconds.toLong()))
        } catch (error: Exception) {
            file.delete()
            recorder?.release()
            recorder = null
            CaptureWidgetState.set(this, "error", error.message ?: "Could not start recording")
            stopSelf()
        }
    }

    @Synchronized
    private fun finishRecording(upload: Boolean): File? {
        handler.removeCallbacks(maximumDuration)
        val file = output
        val recordedLongEnough = SystemClock.elapsedRealtime() - startedElapsed >= 500
        val stopped = runCatching { recorder?.stop() }.isSuccess
        recorder?.release()
        recorder = null
        output = null
        active = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        if (!upload) {
            if (!stopped || !recordedLongEnough) file?.delete()
            CaptureWidgetState.set(this, "ready")
            return file?.takeIf { stopped && recordedLongEnough && it.exists() }
        }
        if (!stopped || !recordedLongEnough || file == null || !file.exists()) {
            file?.delete()
            CaptureWidgetState.set(this, "error", "Recording was too short")
            return null
        }
        CaptureWidgetState.set(this, "uploading", file = file.absolutePath)
        WidgetCaptureUploadWorker.enqueue(this, file)
        return file
    }

    private fun recordingNotification() = NotificationCompat.Builder(this, CHANNEL)
        .setSmallIcon(R.drawable.ic_notification)
        .setContentTitle("Index Inbox is recording")
        .setContentText(if (AuthStore(this).widgetCaptureMode == "review") "Stop and review the transcript in Index Inbox" else "Stop to upload this audio note")
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .addAction(0, "Cancel", serviceIntent(ACTION_CANCEL, 311))
        .addAction(
            0,
            if (AuthStore(this).widgetCaptureMode == "review") "Stop & review" else "Stop & upload",
            if (AuthStore(this).widgetCaptureMode == "review") reviewIntent() else serviceIntent(ACTION_STOP, 312),
        )
        .build()

    private fun serviceIntent(action: String, requestCode: Int) = PendingIntent.getForegroundService(
        this,
        requestCode,
        Intent(this, AudioCaptureService::class.java).setAction(action),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun reviewIntent() = PendingIntent.getActivity(
        this,
        313,
        Intent(this, MainActivity::class.java).apply {
            action = MainActivity.ACTION_WIDGET_REVIEW
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        },
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun createChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Audio capture", NotificationManager.IMPORTANCE_LOW),
        )
    }

    companion object {
        const val ACTION_START = "com.indexinbox.android.widget.START"
        const val ACTION_STOP = "com.indexinbox.android.widget.STOP"
        const val ACTION_CANCEL = "com.indexinbox.android.widget.CANCEL"
        private const val CHANNEL = "index_audio_capture"
        private const val NOTIFICATION_ID = 43
        @Volatile private var active: AudioCaptureService? = null

        fun finishForReview(context: Context): File? {
            val result = active?.finishRecording(upload = false)
            if (result != null) return result
            val stored = CaptureWidgetState.file(context)?.let(::File)?.takeIf(File::exists)
            CaptureWidgetState.set(context, "ready")
            return stored
        }
    }
}

class WidgetCaptureUploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val file = inputData.getString(KEY_FILE)?.let(::File)?.takeIf(File::exists)
            ?: return Result.failure().also { CaptureWidgetState.set(applicationContext, "error", "Recording file is missing") }
        val auth = AuthStore(applicationContext)
        val server = auth.serverUrl
        val token = auth.token
        if (server == null || token == null) {
            CaptureWidgetState.set(applicationContext, "error", "Sign in to upload the recording", file.absolutePath)
            return Result.failure()
        }
        val capture = PendingCapture(
            id = UUID.randomUUID().toString(),
            title = "",
            transcription = "",
            category = auth.widgetCaptureCategory,
            audioPath = file.absolutePath,
            createdAt = System.currentTimeMillis(),
            interpretationAction = "auto",
        )
        val api=ApiFactory.create(server, token)
        return try {
            uploadPendingCapture(api, capture)
            file.delete()
            runCatching {
                val entries=fetchAllEntries(ApiFactory.create(server,token))
                IndexDatabase.get(applicationContext).entries().replaceAll(entries)
                ReminderScheduler.reconcile(applicationContext,entries)
            }
            CaptureWidgetState.set(applicationContext, "saved")
            Result.success()
        } catch (error: Exception) {
            if (error is IOException || error is HttpException && error.code() >= 500) {
                val acknowledged=runCatching {
                    val entries=fetchAllEntries(api)
                    IndexDatabase.get(applicationContext).entries().replaceAll(entries)
                    ReminderScheduler.reconcile(applicationContext,entries)
                    entries.any { it.sourceKey==manualCaptureSourceKey(capture.id) }
                }.getOrDefault(false)
                if(acknowledged) {
                    file.delete()
                    CaptureWidgetState.set(applicationContext,"saved")
                } else {
                    IndexDatabase.get(applicationContext).pending().upsert(capture.copy(lastError = error.message ?: "Offline"))
                    PendingCaptureWorker.schedule(applicationContext)
                    CaptureWidgetState.set(applicationContext, "queued")
                }
                Result.success()
            } else {
                CaptureWidgetState.set(applicationContext, "error", error.message ?: "Upload failed", file.absolutePath)
                Result.failure()
            }
        }
    }

    companion object {
        private const val KEY_FILE = "audio_file"
        fun enqueue(context: Context, file: File) {
            val request = OneTimeWorkRequestBuilder<WidgetCaptureUploadWorker>()
                .setInputData(Data.Builder().putString(KEY_FILE, file.absolutePath).build())
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueue(request)
        }
    }
}

internal fun manualCaptureSourceKey(captureId:String):String =
    MessageDigest.getInstance("SHA-256")
        .digest("manual:$captureId".toByteArray())
        .joinToString(""){"%02x".format(it)}
