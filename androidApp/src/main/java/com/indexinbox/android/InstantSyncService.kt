package com.indexinbox.android

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class InstantSyncService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var connection: Job? = null

    override fun onCreate() {
        super.onCreate()
        NotificationCenter.ensureChannels(this)
        startForeground(CONNECTION_NOTIFICATION_ID, NotificationCenter.connectionNotification(this))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (connection?.isActive != true) connection = scope.launch { connectLoop() }
        return START_STICKY
    }

    override fun onDestroy() {
        connection?.cancel()
        scope.coroutineContext[Job]?.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private suspend fun connectLoop() {
        while (scope.isActive) {
            val auth = AuthStore(this)
            if(!auth.notificationsEnabled||!auth.instantNotifications){stopSelf();return}
            val server = auth.serverUrl
            val token = auth.token
            if (server == null || token == null) {
                stopSelf()
                return
            }
            try {
                val api = ApiFactory.create(server, token)
                val preferences = getSharedPreferences("index_sync", Context.MODE_PRIVATE)
                var sequence = preferences.getLong("change_sequence", -1)
                if (sequence < 0) {
                    sequence = api.changes().sequence
                    preferences.edit().putLong("change_sequence", sequence).apply()
                }
                while (scope.isActive) {
                    val feed = api.waitForChanges(sequence)
                    if (feed.sequence != sequence) {
                        sequence = feed.sequence
                        preferences.edit().putLong("change_sequence", sequence).apply()
                        IndexDatabase.get(this).entries().replaceAll(fetchAllEntries(api))
                    }
                    feed.events.forEach { NotificationCenter.showEvent(this, it) }
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                delay(5_000)
            }
        }
    }

    companion object {
        private const val CONNECTION_NOTIFICATION_ID = 41

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, InstantSyncService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, InstantSyncService::class.java))
        }
    }
}

object NotificationCenter {
    private const val CONNECTION_CHANNEL = "index_inbox_connection"
    private const val ACTIVITY_CHANNEL = "index_inbox_activity"

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CONNECTION_CHANNEL, "Instant connection", NotificationManager.IMPORTANCE_LOW),
        )
        manager.createNotificationChannel(
            NotificationChannel(ACTIVITY_CHANNEL, "Inbox activity", NotificationManager.IMPORTANCE_DEFAULT),
        )
    }

    fun connectionNotification(context: Context) = NotificationCompat.Builder(context, CONNECTION_CHANNEL)
        .setSmallIcon(R.drawable.ic_notification)
        .setColor(0xFFFFCA48.toInt())
        .setContentTitle("Index Inbox connected")
        .setContentText("Listening for new notes on your server")
        .setOngoing(true)
        .setContentIntent(launchIntent(context))
        .build()

    fun showEvent(context: Context, event: ChangeEvent) {
        if(!AuthStore(context).notificationsEnabled)return
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        ensureChannels(context)
        val title = when (event.kind) {
            "capture_standalone" -> "Note received"
            "capture_grouped" -> "Grouped note received"
            "group_created" -> "Group created"
            "webhook_rejected", "ingest_error" -> "Index Inbox needs attention"
            else -> "Index Inbox"
        }
        val entryId=event.details.takeIf {
            event.kind in setOf("capture_standalone","capture_grouped","group_unrecognized")
        }
        context.getSystemService(NotificationManager::class.java).notify(
            1_000 + event.id.toInt(),
            NotificationCompat.Builder(context, ACTIVITY_CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(0xFFFFCA48.toInt())
                .setContentTitle(title)
                .setContentText(event.message)
                .setAutoCancel(true)
                .setContentIntent(launchIntent(context,if(entryId!=null)"entry" else "activity",entryId))
                .build(),
        )
    }

    private fun launchIntent(context:Context,target:String="inbox",entryId:String?=null) = PendingIntent.getActivity(
        context,
        (entryId?.hashCode() ?: target.hashCode()),
        Intent(context,MainActivity::class.java).apply {
            putExtra("notification_target",target)
            entryId?.let{putExtra("entry_id",it)}
        },
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )
}
