package com.predictive.maintenance.kafka;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.predictive.maintenance.service.WebSocketBroadcastService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.KafkaHeaders;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.messaging.handler.annotation.Payload;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * fault-diagnosis-results 토픽 소비 → WebSocket 브로드캐스트
 * (진단 결과를 Grafana/중앙관리 UI에 실시간 반영)
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class FaultDiagnosisResultConsumer {

    private final ObjectMapper objectMapper;
    private final WebSocketBroadcastService wsService;

    @KafkaListener(
            topics = "${kafka.topics.fault-diagnosis-results}",
            groupId = "notification-diagnosis-group"
    )
    public void consume(@Payload String payload,
                        @Header(KafkaHeaders.RECEIVED_TOPIC) String topic) {
        try {
            Map<String, Object> result = objectMapper.readValue(
                    payload, new TypeReference<>() {}
            );
            log.info("진단 결과 수신: event={} model={} top1={}",
                    result.get("anomaly_event_id"),
                    result.get("model"),
                    result.containsKey("root_causes")
                            ? ((java.util.List<?>) result.get("root_causes")).isEmpty()
                                    ? "N/A"
                                    : ((Map<?, ?>) ((java.util.List<?>) result.get("root_causes")).get(0)).get("variable")
                            : "N/A"
            );
            wsService.broadcastDiagnosis(result);
        } catch (Exception e) {
            log.error("진단 결과 처리 실패: {} — {}", payload, e.getMessage());
        }
    }
}
