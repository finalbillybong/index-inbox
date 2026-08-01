package com.indexinbox.android

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import java.util.concurrent.TimeUnit

class ReminderWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun doWork():Result {
        val id=inputData.getString(KEY_ID)?:return Result.failure()
        val early=inputData.getBoolean(KEY_EARLY,false)
        val entry=IndexDatabase.get(applicationContext).entries().get(id)?:return Result.success()
        val due=entry.dueAt?.let{runCatching{Instant.parse(it)}.getOrNull()}?:return Result.success()
        if(entry.reminderCompleted==1||entry.archived==1)return Result.success()
        val trigger=if(early)entry.reminderNotifyBeforeMinutes?.let{due.minusSeconds(it*60L)} else due
        if(trigger==null)return Result.success()
        val marker=ReminderScheduler.marker(entry,early)
        val preferences=ReminderScheduler.preferences(applicationContext)
        if(preferences.getString(ReminderScheduler.markerKey(entry.id,early),null)==marker)return Result.success()
        if(NotificationCenter.showReminder(applicationContext,entry,early)) {
            preferences.edit().putString(ReminderScheduler.markerKey(entry.id,early),marker).apply()
        }
        return Result.success()
    }

    companion object {
        internal const val KEY_ID="entry_id"
        internal const val KEY_EARLY="early"
    }
}

object ReminderScheduler {
    private const val ACTION_DUE="com.indexinbox.android.reminder.DUE"
    private const val ACTION_EARLY="com.indexinbox.android.reminder.EARLY"
    private const val PREFS="index_reminders"
    private const val SCHEDULED="scheduled_ids"

    internal fun preferences(context:Context)=context.getSharedPreferences(PREFS,Context.MODE_PRIVATE)
    internal fun markerKey(id:String,early:Boolean)="${if(early)"early" else "due"}:$id"
    internal fun marker(entry:Entry,early:Boolean)="${entry.dueAt}:${if(early)entry.reminderNotifyBeforeMinutes else 0}"
    private fun workName(id:String,early:Boolean)="index-reminder-${if(early)"early" else "due"}-$id"

    fun reconcile(context:Context,entries:List<Entry>) {
        val preferences=preferences(context)
        val active=entries.filter{it.dueAt!=null&&it.reminderCompleted==0&&it.archived==0}.associateBy{it.id}
        val previous=preferences.getStringSet(SCHEDULED,emptySet()).orEmpty()
        (previous-active.keys).forEach{cancel(context,it)}
        active.values.forEach { entry ->
            schedule(context,entry,early=false)
            if(entry.reminderNotifyBeforeMinutes!=null)schedule(context,entry,early=true)
            else cancel(context,entry.id,early=true)
        }
        preferences.edit().putStringSet(SCHEDULED,active.keys.toSet()).apply()
    }

    private fun schedule(context:Context,entry:Entry,early:Boolean) {
        val due=entry.dueAt?.let{runCatching{Instant.parse(it)}.getOrNull()}?:return
        val trigger=if(early)entry.reminderNotifyBeforeMinutes?.let{due.minusSeconds(it*60L)} else due
        if(trigger==null)return
        if(early&&trigger<=Instant.now()){cancel(context,entry.id,early);return}
        if(preferences(context).getString(markerKey(entry.id,early),null)==marker(entry,early)) {
            cancel(context,entry.id,early);return
        }
        cancel(context,entry.id,early)
        if(scheduleExact(context,entry.id,trigger,early))return
        val delay=Duration.between(Instant.now(),trigger).toMillis().coerceAtLeast(0)
        val work=OneTimeWorkRequestBuilder<ReminderWorker>()
            .setInitialDelay(delay,TimeUnit.MILLISECONDS)
            .setInputData(workerData(entry.id,early)).build()
        WorkManager.getInstance(context).enqueueUniqueWork(workName(entry.id,early),ExistingWorkPolicy.REPLACE,work)
    }

    private fun scheduleExact(context:Context,id:String,trigger:Instant,early:Boolean):Boolean {
        val manager=context.getSystemService(AlarmManager::class.java)
        if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.S&&!manager.canScheduleExactAlarms())return false
        return runCatching {
            manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,trigger.toEpochMilli().coerceAtLeast(System.currentTimeMillis()),alarmIntent(context,id,early))
            true
        }.getOrDefault(false)
    }

    private fun alarmIntent(context:Context,id:String,early:Boolean)=PendingIntent.getBroadcast(
        context,31*id.hashCode()+if(early)1 else 0,
        Intent(context,ReminderAlarmReceiver::class.java).apply {
            action=if(early)ACTION_EARLY else ACTION_DUE
            putExtra(ReminderWorker.KEY_ID,id);putExtra(ReminderWorker.KEY_EARLY,early)
        },PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun workerData(id:String,early:Boolean)=Data.Builder()
        .putString(ReminderWorker.KEY_ID,id).putBoolean(ReminderWorker.KEY_EARLY,early).build()

    fun enqueueImmediate(context:Context,id:String,early:Boolean) {
        val work=OneTimeWorkRequestBuilder<ReminderWorker>().setInputData(workerData(id,early)).build()
        WorkManager.getInstance(context).enqueueUniqueWork(workName(id,early),ExistingWorkPolicy.REPLACE,work)
    }

    private fun cancel(context:Context,id:String) {cancel(context,id,false);cancel(context,id,true)}
    private fun cancel(context:Context,id:String,early:Boolean) {
        context.getSystemService(AlarmManager::class.java).cancel(alarmIntent(context,id,early))
        WorkManager.getInstance(context).cancelUniqueWork(workName(id,early))
    }
}

class ReminderAlarmReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        val id=intent.getStringExtra(ReminderWorker.KEY_ID)?:return
        ReminderScheduler.enqueueImmediate(context,id,intent.getBooleanExtra(ReminderWorker.KEY_EARLY,false))
    }
}

class ReminderBootReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(intent.action !in setOf(Intent.ACTION_BOOT_COMPLETED,Intent.ACTION_MY_PACKAGE_REPLACED))return
        val pending=goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try{ReminderScheduler.reconcile(context,IndexDatabase.get(context).entries().all())}
            finally{pending.finish()}
        }
    }
}
