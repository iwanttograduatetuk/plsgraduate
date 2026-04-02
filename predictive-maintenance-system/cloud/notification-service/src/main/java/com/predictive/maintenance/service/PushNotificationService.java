package com.predictive.maintenance.service;

import com.google.firebase.messaging.*;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * Firebase Cloud Messaging (FCM) 기반 모바일 푸시 알림 서비스.
 *
 * 알림 우선순위:
 *   anomaly_score < 1.5  → INFO    (주의)
 *   anomaly_score < 3.0  → WARNING (경고)
 *   anomaly_score ≥ 3.0  → CRITICAL (긴급)
 *
 * 재배포 확장 포인트:
 *   - APNs 토큰 기반 iOS 알림은 sendToApns() 추가
 *   - 알림 템플릿을 DB 또는 설정 파일로 외부화 가능
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PushNotificationService {

    /**
     * 단일 FCM 토큰으로 푸시 알림 발송.
     *
     * @param fcmToken 수신 디바이스 FCM 토큰
     * @param siteId   지점 ID
     * @param machineId 기계 ID
     * @param subsystem 이상 서브시스템
     * @param anomalyScore 이상 점수
     * @param severity INFO / WARNING / CRITICAL
     * @return FCM 메시지 ID (실패 시 null)
     */
    public String sendToDevice(
            String fcmToken,
            String siteId,
            String machineId,
            String subsystem,
            double anomalyScore,
            String severity
    ) {
        if (fcmToken == null || fcmToken.isBlank()) {
            log.warn("FCM 토큰 없음 — 알림 스킵: {}/{}", siteId, machineId);
            return null;
        }

        String title = buildTitle(severity, siteId);
        String body  = buildBody(machineId, subsystem, anomalyScore);

        Message message = Message.builder()
                .setToken(fcmToken)
                .setNotification(Notification.builder()
                        .setTitle(title)
                        .setBody(body)
                        .build())
                .putData("site_id",       siteId)
                .putData("machine_id",    machineId)
                .putData("subsystem",     subsystem)
                .putData("anomaly_score", String.valueOf(anomalyScore))
                .putData("severity",      severity)
                .setAndroidConfig(AndroidConfig.builder()
                        .setPriority(severity.equals("CRITICAL")
                                ? AndroidConfig.Priority.HIGH
                                : AndroidConfig.Priority.NORMAL)
                        .build())
                .build();

        try {
            String messageId = FirebaseMessaging.getInstance().send(message);
            log.info("FCM 발송 성공: {} → {}/{} [{}]", messageId, siteId, machineId, severity);
            return messageId;
        } catch (FirebaseMessagingException e) {
            log.error("FCM 발송 실패: {}/{} — {}", siteId, machineId, e.getMessage());
            return null;
        }
    }

    /**
     * 여러 FCM 토큰에 동시 발송 (Multicast).
     */
    public BatchResponse sendMulticast(
            List<String> fcmTokens,
            String siteId,
            String machineId,
            String subsystem,
            double anomalyScore,
            String severity
    ) {
        if (fcmTokens == null || fcmTokens.isEmpty()) return null;

        String title = buildTitle(severity, siteId);
        String body  = buildBody(machineId, subsystem, anomalyScore);

        MulticastMessage message = MulticastMessage.builder()
                .addAllTokens(fcmTokens)
                .setNotification(Notification.builder()
                        .setTitle(title)
                        .setBody(body)
                        .build())
                .putData("site_id",    siteId)
                .putData("machine_id", machineId)
                .putData("subsystem",  subsystem)
                .putData("severity",   severity)
                .build();

        try {
            BatchResponse response = FirebaseMessaging.getInstance().sendEachForMulticast(message);
            log.info("FCM Multicast: success={}, failure={}", response.getSuccessCount(), response.getFailureCount());
            return response;
        } catch (FirebaseMessagingException e) {
            log.error("FCM Multicast 실패: {}", e.getMessage());
            return null;
        }
    }

    // ── 메시지 템플릿 ──────────────────────────────────────────────────────────

    private String buildTitle(String severity, String siteId) {
        return switch (severity) {
            case "CRITICAL" -> "🚨 긴급 이상 감지 — " + siteId;
            case "WARNING"  -> "⚠️ 경고 — " + siteId;
            default         -> "ℹ️ 이상 감지 — " + siteId;
        };
    }

    private String buildBody(String machineId, String subsystem, double score) {
        return String.format("%s: %s 서브시스템 이상 (점수: %.2f)", machineId, subsystem, score);
    }
}
