package com.predictive.maintenance.kafka;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.predictive.maintenance.entity.NotificationHistory;
import com.predictive.maintenance.repository.NotificationHistoryRepository;
import com.predictive.maintenance.service.PushNotificationService;
import com.predictive.maintenance.service.WebSocketBroadcastService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.messaging.handler.annotation.Payload;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * notification-events 토픽 소비 → 알림 발송
 *
 * 수신 메시지 포맷:
 * {
 *   "event_id": "uuid",
 *   "site_id": "site-A",
 *   "machine_id": "cnc-001",
 *   "subsystem": "hydraulics",
 *   "anomaly_score": 1.47,
 *   "severity": "WARNING",
 *   "detected_at": "2026-04-01T10:23:45Z"
 * }
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class NotificationEventConsumer {

    private final ObjectMapper objectMapper;
    private final PushNotificationService pushService;
    private final WebSocketBroadcastService wsService;
    private final NotificationHistoryRepository historyRepo;

    // 실제 운영 시 DB에서 관리자 FCM 토큰을 조회하도록 변경
    // 현재는 환경변수 기반 스텁
    private static final List<String> DEMO_FCM_TOKENS = List.of();

    @KafkaListener(
            topics = "${kafka.topics.notification-events}",
            groupId = "notification-consumer-group",
            containerFactory = "kafkaListenerContainerFactory"
    )
    public void consume(@Payload String payload,
                        @Header(KafkaHeaders.RECEIVED_TOPIC) String topic) {
        try {
            Map<String, Object> event = objectMapper.readValue(
                    payload, new TypeReference<>() {}
            );

            String siteId      = (String) event.getOrDefault("site_id", "");
            String machineId   = (String) event.getOrDefault("machine_id", "");
            String subsystem   = (String) event.getOrDefault("subsystem", "");
            double anomalyScore = ((Number) event.getOrDefault("anomaly_score", 0.0)).doubleValue();
            String severity    = (String) event.getOrDefault("severity", "INFO");
            UUID   eventId     = UUID.fromString((String) event.get("event_id"));

            log.info("알림 이벤트 수신: {}/{} subsystem={} severity={}", siteId, machineId, subsystem, severity);

            // 1. WebSocket 실시간 브로드캐스트 (중앙관리자)
            wsService.broadcastAnomaly(event);

            // 2. FCM 모바일 푸시 (지점 관리자)
            String fcmResult = null;
            if (!DEMO_FCM_TOKENS.isEmpty()) {
                // 실 운영: DB에서 해당 지점 관리자의 FCM 토큰 조회
                pushService.sendMulticast(DEMO_FCM_TOKENS, siteId, machineId, subsystem, anomalyScore, severity);
                fcmResult = "SENT";
            } else {
                log.debug("FCM 토큰 없음 — WebSocket만 발송");
                fcmResult = "SKIPPED_NO_TOKEN";
            }

            // 3. 발송 이력 저장
            NotificationHistory history = NotificationHistory.builder()
                    .eventId(eventId)
                    .sentAt(Instant.now())
                    .channel("FCM+WEBSOCKET")
                    .status("SENT")
                    .message(String.format("%s/%s %s 이상 (%.2f)", siteId, machineId, subsystem, anomalyScore))
                    .build();
            historyRepo.save(history);

        } catch (Exception e) {
            log.error("알림 이벤트 처리 실패: {} — {}", payload, e.getMessage());
        }
    }
}
