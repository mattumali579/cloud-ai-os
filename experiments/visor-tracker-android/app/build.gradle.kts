import java.net.URL

plugins {
    id("com.android.application")
}

android {
    namespace = "com.mattumali.visortracker"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.mattumali.visortracker.stable"
        minSdk = 26
        targetSdk = 35
        versionCode = 31
        versionName = "0.30.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Optional persistent signing for future in-place updates. With no stored
    // secret, CI continues producing a normal ephemeral debug APK for tests.
    // Never commit a signing key. Android cannot update an installed APK
    // without the installed APK's original certificate/private key.
    val signingStorePath = System.getenv("VISOR_SIGNING_STORE_FILE").orEmpty()
    if (signingStorePath.isNotBlank()) {
        val signingStore = file(signingStorePath)
        require(signingStore.isFile) { "Stable signing key file missing" }
        val signingStorePassword = System.getenv("VISOR_SIGNING_STORE_PASSWORD").orEmpty()
        val signingAlias = System.getenv("VISOR_SIGNING_KEY_ALIAS").orEmpty()
        val signingKeyPassword = System.getenv("VISOR_SIGNING_KEY_PASSWORD").orEmpty()
        require(signingStorePassword.isNotBlank() && signingAlias.isNotBlank() &&
            signingKeyPassword.isNotBlank()) { "Incomplete stable signing credentials" }
        signingConfigs.create("persistent") {
            storeFile = signingStore
            storePassword = signingStorePassword
            keyAlias = signingAlias
            keyPassword = signingKeyPassword
        }
        buildTypes.getByName("debug") {
            signingConfig = signingConfigs.getByName("persistent")
        }
        buildTypes.getByName("release") {
            signingConfig = signingConfigs.getByName("persistent")
        }
    }

    buildTypes.getByName("release") {
        isMinifyEnabled = false
        isDebuggable = false
        // The signed phone APK stays arm64. Debug and androidTest keep the
        // emulator ABIs that ship inside MediaPipe so a real detector test can run.
        ndk {
            abiFilters += "arm64-v8a"
        }
    }

    // Samsung's installer reports only "App not installed" for a debuggable APK
    // whose native libraries are stored uncompressed. Compress them.
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/visionAssets").get().asFile)
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    implementation("com.google.mediapipe:tasks-vision:0.10.35")
    implementation("com.macedonga.visor:kit:0.1.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("androidx.core:core-ktx:1.15.0")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
}

val visionModels = mapOf(
    "hand_landmarker.task" to "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    "blaze_face_full_range.tflite" to "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/1/blaze_face_full_range.tflite",
    "efficientdet_lite0.tflite" to "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite",
    "efficientdet_lite2.tflite" to "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite2/float16/1/efficientdet_lite2.tflite"
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
