package com.predictive.monitoring.repository;

import com.predictive.monitoring.entity.FaultDiagnosisResult;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface FaultDiagnosisResultRepository extends JpaRepository<FaultDiagnosisResult, UUID> {

    Optional<FaultDiagnosisResult> findByEventId(UUID eventId);
}
