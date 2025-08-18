import React from 'react';
import {
  Box,
  VStack,
  Heading,
  Text,
  Button,
  ButtonGroup,
  NumberInput,
  NumberInputField,
  NumberInputStepper,
  NumberIncrementStepper,
  NumberDecrementStepper,
  HStack,
  Divider,
  Icon,
} from '@chakra-ui/react';
import { FiFilter } from 'react-icons/fi';
import type { DashboardFilters } from '../../lib/api';

interface FilterSidebarProps {
  filters: Partial<DashboardFilters>;
  onFiltersChange: (filters: Partial<DashboardFilters>) => void;
  onApplyFilters: () => void;
  loading: boolean;
}

const FilterSidebar = ({ filters, onFiltersChange, onApplyFilters, loading }: FilterSidebarProps) => {
  const handleFilterChange = (key: keyof DashboardFilters, value: any) => {
    onFiltersChange({
      ...filters,
      [key]: value,
    });
  };

  const timeframeOptions = [
    { label: '30 Days', value: 30 },
    { label: '60 Days', value: 60 },
    { label: '90 Days', value: 90 },
  ];

  return (
    <Box
      position="fixed"
      left={6}
      top={6}
      width="280px"
      height="calc(100vh - 48px)"
      bg="white"
      borderRadius="xl"
      boxShadow="2xl"
      p={6}
      zIndex={1000}
      overflowY="auto"
    >
      <VStack spacing={6} align="stretch">
        {/* Header */}
        <Box>
          <HStack mb={4}>
            <Icon as={FiFilter} color="brand.500" />
            <Heading size="lg" color="gray.700">
              Market Filters
            </Heading>
          </HStack>
        </Box>

        {/* Date Range */}
        <Box>
          <Text fontWeight="semibold" mb={3} fontSize="md" color="gray.700">
            Date Range
          </Text>
          <ButtonGroup size="sm" isAttached variant="outline" w="full">
            {timeframeOptions.map((option) => (
              <Button
                key={option.value}
                flex={1}
                colorScheme={filters.timeframe_days === option.value ? 'brand' : 'gray'}
                variant={filters.timeframe_days === option.value ? 'solid' : 'outline'}
                onClick={() => handleFilterChange('timeframe_days', option.value)}
              >
                {option.label}
              </Button>
            ))}
          </ButtonGroup>
        </Box>

        <Divider />

        {/* Price Range */}
        <Box>
          <Text fontWeight="semibold" mb={3} fontSize="md" color="gray.700">
            Price Range (KM)
          </Text>
          <HStack spacing={3}>
            <Box flex={1}>
              <Text fontSize="sm" color="gray.500" mb={1}>
                Min
              </Text>
              <NumberInput
                value={filters.price_min || 0}
                min={0}
                max={200000}
                step={1000}
                onChange={(valueString) => handleFilterChange('price_min', parseInt(valueString) || 0)}
              >
                <NumberInputField placeholder="0" />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </Box>
            <Box flex={1}>
              <Text fontSize="sm" color="gray.500" mb={1}>
                Max
              </Text>
              <NumberInput
                value={filters.max_price}
                min={1000}
                max={200000}
                step={1000}
                onChange={(valueString) => handleFilterChange('max_price', parseInt(valueString) || 25000)}
              >
                <NumberInputField placeholder="25000" />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </Box>
          </HStack>
        </Box>

        <Divider />

        {/* Year Range */}
        <Box>
          <Text fontWeight="semibold" mb={3} fontSize="md" color="gray.700">
            Year Range
          </Text>
          <HStack spacing={3}>
            <Box flex={1}>
              <Text fontSize="sm" color="gray.500" mb={1}>
                From
              </Text>
              <NumberInput
                value={filters.year_min || 2000}
                min={2000}
                max={2024}
                onChange={(valueString) => handleFilterChange('year_min', parseInt(valueString) || 2000)}
              >
                <NumberInputField placeholder="2000" />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </Box>
            <Box flex={1}>
              <Text fontSize="sm" color="gray.500" mb={1}>
                To
              </Text>
              <NumberInput
                value={filters.year_max || 2024}
                min={2000}
                max={2024}
                onChange={(valueString) => handleFilterChange('year_max', parseInt(valueString) || 2024)}
              >
                <NumberInputField placeholder="2024" />
                <NumberInputStepper>
                  <NumberIncrementStepper />
                  <NumberDecrementStepper />
                </NumberInputStepper>
              </NumberInput>
            </Box>
          </HStack>
        </Box>

        <Divider />

        {/* Transmission */}
        <Box>
          <Text fontWeight="semibold" mb={3} fontSize="md" color="gray.700">
            Transmission
          </Text>
          <ButtonGroup size="sm" isAttached variant="outline" w="full">
            {['Any', 'Auto', 'Manual'].map((option) => (
              <Button
                key={option}
                flex={1}
                colorScheme={filters.transmission === option ? 'brand' : 'gray'}
                variant={filters.transmission === option ? 'solid' : 'outline'}
                onClick={() => handleFilterChange('transmission', option)}
              >
                {option}
              </Button>
            ))}
          </ButtonGroup>
        </Box>

        {/* Apply Filters Button */}
        <Box pt={4}>
          <Button
            colorScheme="brand"
            size="lg"
            w="full"
            onClick={onApplyFilters}
            isLoading={loading}
            loadingText="Applying..."
            leftIcon={<Icon as={FiFilter} />}
          >
            Apply Filters
          </Button>
        </Box>
      </VStack>
    </Box>
  );
};

export default FilterSidebar;