package com.aircontrol.app

import android.content.Context
import android.graphics.Bitmap
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.core.Delegate
import com.google.mediapipe.tasks.vision.core.ImageProcessingOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker

/** MediaPipe Hand Landmarker (una mano, en CPU). Devuelve los 21 puntos como 63 números en la imagen ya vertical. */
class HandTracker(context: Context) {
    private val landmarker: HandLandmarker

    init {
        val base = BaseOptions.builder()
            .setModelAssetPath("hand_landmarker.task")
            .setDelegate(Delegate.CPU)
            .build()
        val options = HandLandmarker.HandLandmarkerOptions.builder()
            .setBaseOptions(base)
            .setRunningMode(RunningMode.VIDEO)
            .setNumHands(1)
            .setMinHandDetectionConfidence(0.55f)
            .setMinHandPresenceConfidence(0.5f)
            .setMinTrackingConfidence(0.5f)
            .build()
        landmarker = HandLandmarker.createFromOptions(context, options)
    }

    /** `rotationDegrees`: cuánto girar la imagen de la cámara para que quede vertical. `timestampMs` debe ir creciendo. */
    fun detect(bitmap: Bitmap, rotationDegrees: Int, timestampMs: Long): FloatArray? {
        val image = BitmapImageBuilder(bitmap).build()
        val processing = ImageProcessingOptions.builder().setRotationDegrees(rotationDegrees).build()
        val result = landmarker.detectForVideo(image, processing, timestampMs)
        val hand = result.landmarks().firstOrNull() ?: return null
        val out = FloatArray(63)
        hand.forEachIndexed { i, p ->
            out[i * 3] = p.x()
            out[i * 3 + 1] = p.y()
            out[i * 3 + 2] = p.z()
        }
        return out
    }

    fun close() = landmarker.close()
}
