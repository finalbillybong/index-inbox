package com.indexinbox.android

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.BroadcastReceiver
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException

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

    suspend fun showEvent(context: Context, event: ChangeEvent) {
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
        val entry=entryId?.let { IndexDatabase.get(context).entries().get(it) }
        val body=notificationBody(entry,event.message)
        val notificationId=1_000 + event.id.toInt()
        val builder=NotificationCompat.Builder(context, ACTIVITY_CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(0xFFFFCA48.toInt())
            .setContentTitle(entry?.title?.takeIf{it.isNotBlank()} ?: title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setAutoCancel(true)
            .setContentIntent(launchIntent(context,if(entryId!=null)"entry" else "activity",entryId))
        if(entryId!=null&&entry!=null) {
            builder
                .addAction(0,"Archive",actionIntent(context,entryId,ACTION_ARCHIVE,notificationId))
                .addAction(0,"Star",actionIntent(context,entryId,ACTION_STAR,notificationId))
                .addAction(0,"Processed",actionIntent(context,entryId,ACTION_PROCESSED,notificationId))
                .addAction(0,"Delete",actionIntent(context,entryId,ACTION_DELETE,notificationId))
        }
        context.getSystemService(NotificationManager::class.java).notify(
            notificationId,
            builder.build(),
        )
    }

    private fun actionIntent(context:Context,entryId:String,action:String,notificationId:Int)=PendingIntent.getBroadcast(
        context,
        31*entryId.hashCode()+action.hashCode(),
        Intent(context,NotificationActionReceiver::class.java).apply {
            this.action=action
            putExtra(EXTRA_ENTRY_ID,entryId)
            putExtra(EXTRA_NOTIFICATION_ID,notificationId)
        },
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun launchIntent(context:Context,target:String="inbox",entryId:String?=null) = PendingIntent.getActivity(
        context,
        (entryId?.hashCode() ?: target.hashCode()),
        Intent(context,MainActivity::class.java).apply {
            putExtra("notification_target",target)
            entryId?.let{putExtra("entry_id",it)}
        },
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    const val ACTION_ARCHIVE="com.indexinbox.android.notification.ARCHIVE"
    const val ACTION_STAR="com.indexinbox.android.notification.STAR"
    const val ACTION_PROCESSED="com.indexinbox.android.notification.PROCESSED"
    const val ACTION_DELETE="com.indexinbox.android.notification.DELETE"
    const val EXTRA_ENTRY_ID="entry_id"
    const val EXTRA_NOTIFICATION_ID="notification_id"
}

class NotificationActionReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        val entryId=intent.getStringExtra(NotificationCenter.EXTRA_ENTRY_ID)?:return
        val action=intent.action?.takeIf {
            it in setOf(
                NotificationCenter.ACTION_ARCHIVE,
                NotificationCenter.ACTION_STAR,
                NotificationCenter.ACTION_PROCESSED,
                NotificationCenter.ACTION_DELETE,
            )
        }?:return
        context.getSystemService(NotificationManager::class.java)
            .cancel(intent.getIntExtra(NotificationCenter.EXTRA_NOTIFICATION_ID,entryId.hashCode()))
        NotificationActionWorker.enqueue(context,entryId,action)
    }
}

class NotificationActionWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun doWork():Result {
        val entryId=inputData.getString(KEY_ENTRY)?:return Result.failure()
        val action=inputData.getString(KEY_ACTION)?:return Result.failure()
        val auth=AuthStore(applicationContext)
        val server=auth.serverUrl?:return Result.failure()
        val token=auth.token?:return Result.failure()
        val api=ApiFactory.create(server,token)
        return try {
            when(action) {
                NotificationCenter.ACTION_ARCHIVE -> api.update(entryId,EntryUpdate(archived=true))
                NotificationCenter.ACTION_STAR -> api.update(entryId,EntryUpdate(starred=true))
                NotificationCenter.ACTION_PROCESSED -> api.update(entryId,EntryUpdate(processed=true))
                NotificationCenter.ACTION_DELETE -> api.delete(entryId)
                else -> return Result.failure()
            }
            IndexDatabase.get(applicationContext).entries().replaceAll(fetchAllEntries(api))
            Result.success()
        } catch (error:Exception) {
            if(error is IOException||error is HttpException&&error.code()>=500)Result.retry() else Result.failure()
        }
    }

    companion object {
        private const val KEY_ENTRY="entry"
        private const val KEY_ACTION="action"
        fun enqueue(context:Context,entryId:String,action:String) {
            val work=OneTimeWorkRequestBuilder<NotificationActionWorker>()
                .setInputData(Data.Builder().putString(KEY_ENTRY,entryId).putString(KEY_ACTION,action).build())
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueue(work)
        }
    }
}

internal fun notificationBody(entry:Entry?,eventMessage:String):String =
    entry?.transcription?.trim().takeUnless{it.isNullOrBlank()}
        ?: if(entry?.audioPath!=null)"Audio note received. Transcription may still be processing." else eventMessage
