package com.predictive.monitoring.repository;

import com.predictive.monitoring.entity.Machine;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface MachineRepository extends JpaRepository<Machine, String> {

    List<Machine> findBySiteIdOrderByMachineId(String siteId);

    long countBySiteIdAndStatus(String siteId, String status);

    long countByStatus(String status);
}
