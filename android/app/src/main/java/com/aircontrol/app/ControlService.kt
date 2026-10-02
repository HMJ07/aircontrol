package com.aircontrol.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.app.Notification
import android.os.SystemClock
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Servicio en primer plano (con la cámara frontal): cada fotograma se detecta la mano con MediaPipe y se la pasa al motor Python,
 * que decide y ejecuta el resultado con el Servicio de Accesibilidad.
 */
class ControlService : LifecycleService() {
    companion object {
        const val ACTION_STOP = "com.aircontrol.app.STOP"
        private const val CHANNEL = "control"
        private const val TAG = "AirControl"
        @Volatile var running = false
        @Volatile private var module: PyObject? = null

        /** Órdenes desde la pantalla principal: "toggle_pause", "pause", "resume". */
        fun command(name: String) {
            try { module?.callAttr("command", name) } catch (t: Throwable) { Log.e(TAG, "command", t) }
        }
    }

    private var tracker: HandTracker? = null
    private var executor: ExecutorService? = null
    private var provider: ProcessCameraProvider? = null
    private var lastTs = 0L

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startInForeground()
        if (!running) start()
        return START_NOT_STICKY
    }

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "AirControl", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(
            this, 0, Intent(this, ControlService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val open = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentTitle("AirControl está activo")
            .setContentText("La cámara frontal sigue tu mano. Toca para abrir.")
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(Icon.createWithResource(this, android.R.drawable.ic_media_pause), "Detener", stop).build())
            .setOngoing(true)
            .build()
        ServiceCompat.startForeground(this, 1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
    }

    private fun start() {
        try {
            PhoneBridge.initScreen(this)
            if (!Python.isStarted()) Python.start(AndroidPlatform(this))
            val py = Python.getInstance().getModule("android_main")
            py.callAttr("start", PhoneBridge, filesDir.absolutePath, PhoneBridge.screenW, PhoneBridge.screenH)
            module = py
            tracker = HandTracker(this)
            executor = Executors.newSingleThreadExecutor()
        } catch (t: Throwable) {
            Log.e(TAG, "No se pudo iniciar el motor", t)
            stopSelf()
            return
        }
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val camera = future.get()
                provider = camera
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                    .build()
                analysis.setAnalyzer(executor!!) { image -> analyze(image) }
                camera.unbindAll()
                camera.bindToLifecycle(this, CameraSelector.DEFAULT_FRONT_CAMERA, analysis)
                running = true
            } catch (t: Throwable) {
                Log.e(TAG, "No se pudo abrir la cámara frontal", t)
                stopSelf()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(image: ImageProxy) {
        try {
            val rotation = image.imageInfo.rotationDegrees
            val bitmap = image.toBitmap()
            val ts = maxOf(SystemClock.uptimeMillis(), lastTs + 1)
            lastTs = ts
            val hand = tracker?.detect(bitmap, rotation, ts)
            val upright = rotation % 180 == 0
            val w = if (upright) image.width else image.height
            val h = if (upright) image.height else image.width
            module?.callAttr("process", hand, w, h, ts.toDouble())
        } catch (t: Throwable) {
            Log.e(TAG, "fotograma", t)
        } finally {
            image.close()
        }
    }

    override fun onDestroy() {
        running = false
        try { provider?.unbindAll() } catch (_: Throwable) {}
        try { module?.callAttr("stop", SystemClock.uptimeMillis().toDouble()) } catch (_: Throwable) {}
        module = null
        executor?.shutdown()
        tracker?.close()
        super.onDestroy()
    }
}
