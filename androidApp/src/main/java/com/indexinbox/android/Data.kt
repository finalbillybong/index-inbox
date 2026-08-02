package com.indexinbox.android

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.PrimaryKey
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Transaction
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

@Dao
interface EntryDao {
    @Query("SELECT * FROM entries ORDER BY createdAt DESC")
    fun observeInbox(): Flow<List<Entry>>

    @Query("SELECT * FROM entries WHERE id = :id")
    fun observe(id: String): Flow<Entry?>

    @Query("SELECT * FROM entries WHERE id = :id")
    suspend fun get(id: String): Entry?

    @Query("SELECT * FROM entries")
    suspend fun all(): List<Entry>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entries: List<Entry>)

    @Query("DELETE FROM entries")
    suspend fun clear()

    @Transaction
    suspend fun replaceAll(entries: List<Entry>) {
        clear()
        upsert(entries)
    }

    @Query("DELETE FROM entries WHERE id = :id")
    suspend fun delete(id: String)
}

@Entity(tableName="pending_captures")
data class PendingCapture(
    @PrimaryKey val id: String,
    val title: String,
    val transcription: String,
    val category: String,
    val audioPath: String? = null,
    val createdAt: Long,
    val lastError: String = "",
    val interpretationAction: String? = null,
)

@Dao
interface PendingCaptureDao {
    @Query("SELECT * FROM pending_captures ORDER BY createdAt")
    fun observeAll(): Flow<List<PendingCapture>>

    @Query("SELECT * FROM pending_captures ORDER BY createdAt")
    suspend fun all(): List<PendingCapture>

    @Insert(onConflict=OnConflictStrategy.REPLACE)
    suspend fun upsert(capture: PendingCapture)

    @Query("DELETE FROM pending_captures WHERE id=:id")
    suspend fun delete(id: String)
}

@Database(entities = [Entry::class,PendingCapture::class], version = 6, exportSchema = false)
abstract class IndexDatabase : RoomDatabase() {
    abstract fun entries(): EntryDao
    abstract fun pending(): PendingCaptureDao

    companion object {
        @Volatile private var instance: IndexDatabase? = null
        fun get(context: Context): IndexDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context, IndexDatabase::class.java, "index-inbox.db")
                .addMigrations(MIGRATION_1_2,MIGRATION_2_3,MIGRATION_3_4,MIGRATION_4_5,MIGRATION_5_6)
                .build().also { instance = it }
        }
        private val MIGRATION_1_2=object:Migration(1,2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""CREATE TABLE IF NOT EXISTS pending_captures (
                    id TEXT NOT NULL PRIMARY KEY,
                    title TEXT NOT NULL,
                    transcription TEXT NOT NULL,
                    category TEXT NOT NULL,
                    audioPath TEXT,
                    createdAt INTEGER NOT NULL,
                    lastError TEXT NOT NULL DEFAULT ''
                )""")
            }
        }
        private val MIGRATION_2_3=object:Migration(2,3) {
            override fun migrate(db:SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE entries ADD COLUMN dueAt TEXT")
                db.execSQL("ALTER TABLE entries ADD COLUMN reminderCompleted INTEGER NOT NULL DEFAULT 0")
            }
        }
        private val MIGRATION_3_4=object:Migration(3,4) {
            override fun migrate(db:SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE entries ADD COLUMN reminderNotifyBeforeMinutes INTEGER")
            }
        }
        private val MIGRATION_4_5=object:Migration(4,5) {
            override fun migrate(db:SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE entries ADD COLUMN completed INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE entries ADD COLUMN collectionName TEXT")
            }
        }
        private val MIGRATION_5_6=object:Migration(5,6) {
            override fun migrate(db:SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE pending_captures ADD COLUMN interpretationAction TEXT")
            }
        }
    }
}
