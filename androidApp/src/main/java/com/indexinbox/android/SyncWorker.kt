package com.indexinbox.android

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

class SyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val auth = AuthStore(applicationContext)
        val server = auth.serverUrl ?: return Result.success()
        val token = auth.token ?: return Result.success()
        return try {
            val api = ApiFactory.create(server, token)
            val preferences = applicationContext.getSharedPreferences("index_sync", Context.MODE_PRIVATE)
            val previous = preferences.getLong("change_sequence", -1)
            val feed = api.changes(if (previous >= 0) previous else null)
            preferences.edit().putLong("change_sequence", feed.sequence).apply()
            IndexDatabase.get(applicationContext).entries().replaceAll(fetchAllEntries(api))
            if (previous >= 0 && auth.notificationsEnabled) feed.events.forEach { NotificationCenter.showEvent(applicationContext,it) }
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    companion object {
        private const val WORK_NAME = "index-inbox-background-sync"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            WorkManager.getInstance(context).enqueue(
                OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(constraints).build(),
            )
            val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.UPDATE,
                request,
            )
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
            context.getSharedPreferences("index_sync", Context.MODE_PRIVATE).edit().clear().apply()
        }
    }
}
