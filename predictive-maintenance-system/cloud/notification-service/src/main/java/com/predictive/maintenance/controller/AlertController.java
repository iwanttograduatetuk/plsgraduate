package com.predictive.maintenance.controller;

import com.predictive.maintenance.entity.NotificationHistory;
import com.predictive.maintenance.repository.NotificationHistoryRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 알림 이력 조회 REST API
 */
@Slf4j
@RestController
@RequestMapping("/api/alerts")
@RequiredArgsConstructor
public class AlertController {

    private final NotificationHistoryRepository historyRepo;

    /** 최근 알림 이력 (페이징) */
    @GetMapping
    public ResponseEntity<Page<NotificationHistory>> getAlerts(
            @RequestParam(defaultValue = "0")  int page,
            @RequestParam(defaultValue = "20") int size
    ) {
        PageRequest pageable = PageRequest.of(page, size, Sort.by("sentAt").descending());
        return ResponseEntity.ok(historyRepo.findAllByOrderBySentAtDesc(pageable));
    }

    /** 특정 이벤트의 알림 이력 */
    @GetMapping("/event/{eventId}")
    public ResponseEntity<List<NotificationHistory>> getByEvent(
            @PathVariable UUID eventId
    ) {
        return ResponseEntity.ok(historyRepo.findByEventIdOrderBySentAtDesc(eventId));
    }

    /** 발송 통계 */
    @GetMapping("/stats")
    public ResponseEntity<Map<String, Long>> getStats() {
        return ResponseEntity.ok(Map.of(
                "total",  historyRepo.count(),
                "sent",   historyRepo.countByStatus("SENT"),
                "failed", historyRepo.countByStatus("FAILED")
        ));
    }

    /** 헬스체크 */
    @GetMapping("/health")
    public ResponseEntity<Map<String, String>> health() {
        return ResponseEntity.ok(Map.of("status", "ok", "service", "notification-service"));
    }
}
