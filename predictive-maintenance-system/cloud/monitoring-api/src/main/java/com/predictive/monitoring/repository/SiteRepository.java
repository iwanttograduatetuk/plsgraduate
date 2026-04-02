package com.predictive.monitoring.repository;

import com.predictive.monitoring.entity.Site;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface SiteRepository extends JpaRepository<Site, String> {

    @Query("""
        SELECT s FROM Site s
        LEFT JOIN FETCH s.machines m
        ORDER BY s.siteId
        """)
    List<Site> findAllWithMachines();
}
