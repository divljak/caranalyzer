import React from 'react';
import {
  Box,
  SimpleGrid,
  Heading,
  Text,
  VStack,
  Icon,
} from '@chakra-ui/react';
import { FiZap, FiAlertTriangle, FiTrendingUp } from 'react-icons/fi';
import type { OpportunityCard as OpportunityCardType } from '../../lib/api';

interface OpportunityCardProps {
  opportunity: OpportunityCardType;
}

const OpportunityCard = ({ opportunity }: OpportunityCardProps) => {
  const getCardColors = (color: string) => {
    switch (color) {
      case 'green':
        return {
          bg: 'success.50',
          borderColor: 'success.200',
          textColor: 'success.800',
          icon: FiZap,
        };
      case 'orange':
        return {
          bg: 'warning.50',
          borderColor: 'warning.200',
          textColor: 'warning.800',
          icon: FiAlertTriangle,
        };
      case 'blue':
        return {
          bg: 'brand.50',
          borderColor: 'brand.200',
          textColor: 'brand.800',
          icon: FiTrendingUp,
        };
      default:
        return {
          bg: 'gray.50',
          borderColor: 'gray.200',
          textColor: 'gray.800',
          icon: FiZap,
        };
    }
  };

  const colors = getCardColors(opportunity.color);

  return (
    <Box
      bg={colors.bg}
      borderColor={colors.borderColor}
      borderWidth="2px"
      borderRadius="xl"
      p={5}
      cursor="pointer"
      transition="all 0.3s ease"
      _hover={{
        transform: 'translateY(-5px)',
        boxShadow: 'xl',
      }}
    >
      <VStack align="start" spacing={2}>
        <Box display="flex" alignItems="center">
          <Icon as={colors.icon} mr={2} color={colors.textColor} />
          <Heading size="md" color={colors.textColor} fontWeight="bold">
            {opportunity.title}
          </Heading>
        </Box>
        <Text fontSize="sm" color={colors.textColor} lineHeight="1.4">
          {opportunity.subtitle}
        </Text>
      </VStack>
    </Box>
  );
};

interface TopOpportunitiesProps {
  opportunities: OpportunityCardType[];
}

const TopOpportunities = ({ opportunities }: TopOpportunitiesProps) => {
  return (
    <Box mb={8}>
      <Heading size="xl" mb={6} color="gray.800">
        Top Opportunities
      </Heading>
      <SimpleGrid columns={{ base: 1, md: 3 }} spacing={6}>
        {opportunities.map((opportunity, index) => (
          <OpportunityCard key={index} opportunity={opportunity} />
        ))}
      </SimpleGrid>
    </Box>
  );
};

export default TopOpportunities;