import java.net.URL

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.mattumali.visortracker"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.mattumali.visortracker.stream"
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "0.3.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/visionAssets"))
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    testOptions { unitTests.isReturnDefaultValues = true }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("com.google.mediapipe:tasks-vision:0.10.35")
    implementation("com.macedonga.visor:kit:0.1.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    testImplementation("junit:junit:4.13.2")
}

val visionModels = mapOf(
    "hand_landmarker.task" to "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    "blaze_face_full_range.tflite" to "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/1/blaze_face_full_range.tflite",
    "efficientdet_lite0.tflite" to "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite"
)

val downloadVisionModels by tasks.registering {
    val assetsDir = layout.buildDirectory.dir("generated/visionAssets")
    outputs.files(visionModels.keys.map { assetsDir.map { dir -> dir.file(it) } })
    doLast {
        val destination = assetsDir.get().asFile
        destination.mkdirs()
        visionModels.forEach { (name, source) ->
            val output = destination.resolve(name)
            if (!output.exists() || output.length() == 0L) {
                val connection = URL(source).openConnection().apply {
                    connectTimeout = 20_000
                    readTimeout = 60_000
                }
                connection.getInputStream().use { input ->
                    output.outputStream().use { stream -> input.copyTo(stream) }
                }
            }
            check(output.length() > 0L) { "Downloaded empty MediaPipe model: $name" }
        }
    }
}

tasks.named("preBuild").configure { dependsOn(downloadVisionModels) }
