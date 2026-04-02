package com.predictive.maintenance.config;

import com.google.auth.oauth2.GoogleCredentials;
import com.google.firebase.FirebaseApp;
import com.google.firebase.FirebaseOptions;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;

import jakarta.annotation.PostConstruct;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * Firebase Admin SDK 초기화.
 *
 * firebase-service-account.json 파일을 환경변수 FIREBASE_CREDENTIALS_FILE로 지정.
 * 파일이 없으면 FCM 기능은 비활성화되고 나머지 기능은 정상 동작.
 */
@Slf4j
@Configuration
public class FirebaseConfig {

    @Value("${firebase.credentials-file:firebase-service-account.json}")
    private String credentialsFile;

    @PostConstruct
    public void initFirebase() {
        if (FirebaseApp.getApps().isEmpty()) {
            try {
                InputStream serviceAccount;
                if (Files.exists(Paths.get(credentialsFile))) {
                    serviceAccount = new FileInputStream(credentialsFile);
                    FirebaseOptions options = FirebaseOptions.builder()
                            .setCredentials(GoogleCredentials.fromStream(serviceAccount))
                            .build();
                    FirebaseApp.initializeApp(options);
                    log.info("Firebase 초기화 완료: {}", credentialsFile);
                } else {
                    log.warn("Firebase 자격증명 파일 없음 ({}). FCM 비활성화.", credentialsFile);
                }
            } catch (IOException e) {
                log.error("Firebase 초기화 실패: {}", e.getMessage());
            }
        }
    }
}
