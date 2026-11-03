plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
}

android {
    namespace = "dev.orbit.companion"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.orbit.companion"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    // 고정 서명 키. 환경 변수가 있으면(CI 비밀값) 그 키로 서명해서 빌드마다 서명이 바뀌지 않게 한다.
    // 서명이 바뀌면 폰에서 덮어 설치가 안 되고 앱을 지워야 한다. 환경 변수가 없으면 로컬 기본 디버그 키를 쓴다
    val orbitKeystore = System.getenv("ORBIT_KEYSTORE_PATH")?.let { project.file(it) }?.takeIf { it.exists() }
    signingConfigs {
        if (orbitKeystore != null) {
            create("orbit") {
                storeFile = orbitKeystore
                storeType = "PKCS12"
                storePassword = System.getenv("ORBIT_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ORBIT_KEY_ALIAS") ?: "orbit"
                keyPassword = System.getenv("ORBIT_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            if (orbitKeystore != null) signingConfig = signingConfigs.getByName("orbit")
        }
        release {
            isMinifyEnabled = false
            if (orbitKeystore != null) signingConfig = signingConfigs.getByName("orbit")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

ksp {
    arg("room.schemaLocation", "$projectDir/schemas")
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)
    implementation(libs.androidx.work.runtime.ktx)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
}
