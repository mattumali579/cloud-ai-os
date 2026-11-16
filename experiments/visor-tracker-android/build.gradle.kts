plugins {
    id("com.android.application") version "9.4.1" apply false
    // AGP built-in Kotlin pulls KGP 2.2 by default. VDK 0.1.1 was compiled with 2.4.20.
    id("org.jetbrains.kotlin.android") version "2.4.20" apply false
}
