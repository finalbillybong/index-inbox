package com.indexinbox.android

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.MultipartBody
import okhttp3.RequestBody
import okhttp3.ResponseBody
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Multipart
import retrofit2.http.Part
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming
import okhttp3.MediaType.Companion.toMediaType

interface IndexApi {
    @POST("auth/device/login")
    suspend fun login(@Body request: DeviceLoginRequest): DeviceLoginResponse

    @POST("auth/device/logout")
    suspend fun logout(): ApiResult

    @GET("api/items")
    suspend fun entries(
        @Query("page") page: Int = 1,
        @Query("limit") limit: Int = 100,
        @Query("q") query: String? = null,
    ): EntryPage

    @PATCH("api/items/{id}")
    suspend fun update(@Path("id") id: String, @Body update: EntryUpdate): ApiResult

    @PATCH("api/items/{id}")
    suspend fun assignGroup(@Path("id") id: String, @Body update: JsonObject): ApiResult

    @DELETE("api/items/{id}")
    suspend fun delete(@Path("id") id: String): ApiResult

    @POST("api/manual")
    suspend fun capture(@Body capture: ManualCapture): ApiResult

    @POST("api/interpret")
    suspend fun interpret(@Body request: InterpretationRequest): InterpretationResult

    @Multipart
    @POST("api/manual")
    suspend fun captureAudio(
        @Part audio: MultipartBody.Part,
        @Part("transcription") transcription: RequestBody,
        @Part("title") title: RequestBody,
        @Part("category") category: RequestBody,
        @Part("recordedAt") recordedAt: RequestBody,
        @Part("id") captureId: RequestBody,
        @Part("interpretationAction") interpretationAction: RequestBody,
    ): ApiResult

    @GET("api/changes")
    suspend fun changes(@Query("since") since: Long? = null): ChangeFeed

    @GET("api/changes/wait")
    suspend fun waitForChanges(@Query("since") since: Long, @Query("timeout") timeout: Int = 25): ChangeFeed

    @Multipart
    @POST("api/transcribe")
    suspend fun transcribe(@Part audio: MultipartBody.Part): TranscriptionResult

    @GET("api/collections")
    suspend fun groups(): List<NoteGroup>

    @POST("api/collections")
    suspend fun createCollection(@Body request: CreateCollectionRequest): NoteGroup

    @GET("api/activity")
    suspend fun activity(): List<ActivityItem>

    @GET("api/automation")
    suspend fun automation(): AutomationSettings

    @PATCH("api/automation")
    suspend fun updateAutomation(@Body update:AutomationUpdate):AutomationSettings

    @GET("api/model")
    suspend fun interpretationModel():InterpretationModelSettings

    @PATCH("api/model")
    suspend fun updateInterpretationModel(@Body update:InterpretationModelUpdate):InterpretationModelSettings

    @POST("api/model/test")
    suspend fun testInterpretationModel():InterpretationModelSettings

    @POST("api/operations/{id}/undo")
    suspend fun undoOperation(@Path("id") id:String):UndoOperationResult

    @POST("api/operations/{id}/confirm")
    suspend fun confirmOperation(@Path("id") id:String):UndoOperationResult

    @GET("api/collections/{name}/timeline")
    suspend fun groupTimeline(@Path("name") name: String): GroupTimeline

    @PATCH("api/collections/{name}")
    suspend fun updateGroup(@Path("name") name: String, @Body update: GroupUpdate): GroupUpdateResult

    @DELETE("api/collections/{name}")
    suspend fun deleteGroup(@Path("name") name: String, @Query("ungroup") ungroup: Boolean = true): ApiResult

    @GET("api/collections/{name}/aliases")
    suspend fun groupAliases(@Path("name") name: String): GroupAliases

    @POST("api/collections/{name}/aliases")
    suspend fun addAlias(@Path("name") name: String, @Body request: AliasRequest): ApiResult

    @retrofit2.http.HTTP(method = "DELETE", path = "api/collections/{name}/aliases", hasBody = true)
    suspend fun removeAlias(@Path("name") name: String, @Body request: AliasRequest): ApiResult

    @GET("api/collection-suggestions")
    suspend fun suggestions(): List<GroupSuggestion>

    @POST("api/collection-suggestions/{id}/{action}")
    suspend fun resolveSuggestion(@Path("id") id: String, @Path("action") action: String, @Body request: SuggestionRequest): ApiResult

    @POST("api/items/bulk")
    suspend fun bulk(@Body request: BulkRequest): ApiResult

    @retrofit2.http.HTTP(method = "DELETE", path = "api/items", hasBody = true)
    suspend fun deleteBulk(@Body request: BulkRequest): ApiResult

    @GET("api/status")
    suspend fun status(): ServerStatus

    @POST("api/backups")
    suspend fun createBackup(): BackupResult

    @POST("api/maintenance/retention")
    suspend fun retention(@Body request: RetentionRequest): RetentionResult

    @Streaming
    @GET("api/export/{format}")
    suspend fun export(@Path("format") format: String): ResponseBody

    @Streaming
    @GET("api/collections/{name}/export/{format}")
    suspend fun groupExport(@Path("name") name: String, @Path("format") format: String): ResponseBody

    @Streaming
    @GET("api/backups/latest")
    suspend fun latestBackup(): ResponseBody

    @POST("api/backup-hook")
    suspend fun triggerBackupHook(): ApiResult

    @GET("api/integrations/index-ring")
    suspend fun indexRingIntegration(): IndexRingIntegration

    @POST("api/integrations/index-ring/reveal")
    suspend fun revealIndexRingSecret(@Body request: IntegrationPasswordRequest): IntegrationSecret

    @POST("api/integrations/index-ring/rotate")
    suspend fun rotateIndexRingSecret(@Body request: IntegrationPasswordRequest): IntegrationSecret

    @POST("webhook/index")
    suspend fun testIndexRing(@Header("X-Webhook-Secret") secret: String, @Body capture: ManualCapture): ApiResult

    @Streaming
    @GET("api/items/{id}/audio")
    suspend fun audioDownload(@Path("id") id: String): ResponseBody

    @GET("auth/devices")
    suspend fun devices(): List<DeviceSession>

    @POST("auth/devices/revoke-others")
    suspend fun revokeOtherDevices(): RevokeDevicesResult

    @GET("api/android-update")
    suspend fun androidUpdate(): AndroidUpdate

    @Streaming
    @GET("api/android-update/apk")
    suspend fun androidUpdateApk(): ResponseBody
}

object ApiFactory {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    fun create(serverUrl: String, token: String? = null): IndexApi {
        val auth = Interceptor { chain ->
            val request = chain.request().newBuilder().apply {
                if (!token.isNullOrBlank()) header("Authorization", "Bearer $token")
            }.build()
            chain.proceed(request)
        }
        val logging = HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
        val client = OkHttpClient.Builder().addInterceptor(auth).addInterceptor(logging).build()
        return Retrofit.Builder()
            .baseUrl(AuthStore.normalizeUrl(serverUrl))
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(IndexApi::class.java)
    }
}

suspend fun fetchAllEntries(api: IndexApi): List<Entry> {
    val result=mutableListOf<Entry>()
    var page=1
    do {
        val response=api.entries(page=page,limit=200)
        result+=response.items
        page++
    } while(page<=response.pages)
    return result
}
