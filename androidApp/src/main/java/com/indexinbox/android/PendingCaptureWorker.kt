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
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.util.concurrent.TimeUnit

class PendingCaptureWorker(context: Context,params: WorkerParameters):CoroutineWorker(context,params) {
    override suspend fun doWork(): Result {
        val auth=AuthStore(applicationContext)
        val server=auth.serverUrl?:return Result.success()
        val token=auth.token?:return Result.success()
        val database=IndexDatabase.get(applicationContext)
        val api=ApiFactory.create(server,token)
        return try {
            for(capture in database.pending().all()) {
                uploadPendingCapture(api,capture)
                val widgetCapture=capture.audioPath?.let(::File)?.parentFile?.name=="widget-audio"
                capture.audioPath?.let { File(it).delete() }
                database.pending().delete(capture.id)
                if(widgetCapture)CaptureWidgetState.set(applicationContext,"saved")
            }
            database.entries().replaceAll(fetchAllEntries(api))
            Result.success()
        } catch (_:Exception) {
            Result.retry()
        }
    }

    companion object {
        private const val WORK_NAME="index-inbox-pending-captures"
        fun schedule(context: Context) {
            val constraints=Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            WorkManager.getInstance(context).enqueue(OneTimeWorkRequestBuilder<PendingCaptureWorker>().setConstraints(constraints).build())
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,ExistingPeriodicWorkPolicy.UPDATE,
                PeriodicWorkRequestBuilder<PendingCaptureWorker>(15,TimeUnit.MINUTES).setConstraints(constraints).build(),
            )
        }
    }
}

suspend fun uploadPendingCapture(api: IndexApi,capture: PendingCapture) {
    val audio=capture.audioPath?.let(::File)?.takeIf(File::exists)
    if(audio==null) {
        api.capture(ManualCapture(capture.transcription,capture.title,capture.category,capture.createdAt,capture.id))
        return
    }
    val plain="text/plain".toMediaType()
    api.captureAudio(
        MultipartBody.Part.createFormData("audio","recording.m4a",audio.asRequestBody("audio/mp4".toMediaType())),
        capture.transcription.toRequestBody(plain),
        capture.title.toRequestBody(plain),
        capture.category.toRequestBody(plain),
        capture.createdAt.toString().toRequestBody(plain),
        capture.id.toRequestBody(plain),
    )
}
