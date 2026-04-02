package com.predictive.monitoring.repository;

import com.predictive.monitoring.entity.AnomalyEvent;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

@Repository
public interface AnomalyEventRepository extends JpaRepository<AnomalyEvent, UUID> {

    Page<AnomalyEvent> findAllByOrderByDetectedAtDesc(Pageable pageable);

    Page<AnomalyEvent> findBySiteIdOrderByDetectedAtDesc(String siteId, Pageable pageable);

    Page<AnomalyEvent> findByMachineIdOrderByDetectedAtDesc(String machineId, Pageable pageable);

    @Query("""
        SELECT e FROM AnomalyEvent e
        WHERE e.detectedAt >= :from
        ORDER BY e.detectedAt DESC
        """)
    List<AnomalyEvent> findRecentSince(@Param("from") Instant from);

    long countBySeverityAndIsResolved(String severity, Boolean isResolved);

    long countByIsResolved(Boolean isResolved);
}
