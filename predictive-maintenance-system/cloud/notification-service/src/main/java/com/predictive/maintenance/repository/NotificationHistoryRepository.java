package com.predictive.maintenance.repository;

import com.predictive.maintenance.entity.NotificationHistory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface NotificationHistoryRepository extends JpaRepository<NotificationHistory, UUID> {

    List<NotificationHistory> findByEventIdOrderBySentAtDesc(UUID eventId);

    Page<NotificationHistory> findAllByOrderBySentAtDesc(Pageable pageable);

    long countByStatus(String status);
}
