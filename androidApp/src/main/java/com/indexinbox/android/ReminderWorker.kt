package com.indexinbox.android

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.Duration
import java.time.Instant
import java.util.concurrent.TimeUnit

class ReminderWorker(context:Context,params:WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun doWork():Result {
        val id=inputData.getString(KEY_ID)?:return Result.failure()
        val entry=IndexDatabase.get(applicationContext).entries().get(id)?:return Result.success()
        if(entry.dueAt==null||entry.reminderCompleted==1||entry.archived==1)return Result.success()
        if(NotificationCenter.showReminder(applicationContext,entry)) {
            reminderPreferences(applicationContext).edit().putString(entry.id,entry.dueAt).apply()
        }
        return Result.success()
    }

    companion object {
        private const val KEY_ID="entry_id"
        private fun workName(id:String)="index-reminder-$id"

        fun reconcile(context:Context,entries:List<Entry>) {
            val manager=WorkManager.getInstance(context)
            val preferences=reminderPreferences(context)
            entries.forEach { entry ->
                val due=entry.dueAt?.let { runCatching{Instant.parse(it)}.getOrNull() }
                if(due==null||entry.reminderCompleted==1||entry.archived==1) {
                    manager.cancelUniqueWork(workName(entry.id))
                    preferences.edit().remove(entry.id).apply()
                } else if(preferences.getString(entry.id,null)==entry.dueAt) {
                    manager.cancelUniqueWork(workName(entry.id))
                } else {
                    val delay=Duration.between(Instant.now(),due).toMillis().coerceAtLeast(0)
                    val work=OneTimeWorkRequestBuilder<ReminderWorker>()
                        .setInitialDelay(delay,TimeUnit.MILLISECONDS)
                        .setInputData(Data.Builder().putString(KEY_ID,entry.id).build())
                        .build()
                    manager.enqueueUniqueWork(workName(entry.id),ExistingWorkPolicy.REPLACE,work)
                }
            }
        }

        private fun reminderPreferences(context:Context)=
            context.getSharedPreferences("index_reminders",Context.MODE_PRIVATE)
    }
}
