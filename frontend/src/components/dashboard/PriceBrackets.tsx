import React from 'react';
import {
  Box,
  Heading,
  Text,
  VStack,
  HStack,
  Spacer,
  Accordion,
  AccordionItem,
  AccordionButton,
  AccordionPanel,
  AccordionIcon,
  Badge,
  Link,
  Icon,
} from '@chakra-ui/react';
import { FiExternalLink } from 'react-icons/fi';
import type { PriceBracket } from '../../lib/api';

interface PriceBracketsProps {
  priceBrackets: PriceBracket[];
}

const PriceBrackets = ({ priceBrackets }: PriceBracketsProps) => {
  return (
    <Box mb={8}>
      <Heading size="xl" mb={6} color="gray.800">
        Market Insights by Price Bracket
      </Heading>
      
      <Box bg="white" borderRadius="xl" boxShadow="lg" overflow="hidden">
        <Accordion allowMultiple>
          {priceBrackets.map((bracket, index) => (
            <AccordionItem key={index} border="none">
              <AccordionButton
                py={4}
                px={6}
                _hover={{ bg: 'gray.50' }}
                _expanded={{ bg: 'blue.50', borderColor: 'blue.200' }}
              >
                <Box flex="1" textAlign="left">
                  <HStack spacing={4}>
                    <Text fontSize="lg" fontWeight="bold" color="gray.800">
                      {bracket.bracket_name}
                    </Text>
                    <Badge colorScheme="blue" fontSize="xs">
                      {bracket.total_cars} cars
                    </Badge>
                    <Badge colorScheme="green" fontSize="xs">
                      {bracket.total_models} models
                    </Badge>
                  </HStack>
                </Box>
                <AccordionIcon />
              </AccordionButton>
              
              <AccordionPanel pb={4} px={6}>
                {bracket.models && bracket.models.length > 0 ? (
                  <VStack align="stretch" spacing={3}>
                    <Text fontSize="sm" color="gray.600" mb={2}>
                      Top performing models (fastest selling):
                    </Text>
                    {bracket.models.map((model) => (
                      <VStack
                        key={`${model.rank}-${model.model}`}
                        py={3}
                        px={4}
                        bg="gray.50"
                        borderRadius="lg"
                        borderLeft="4px solid"
                        borderColor="blue.400"
                        align="stretch"
                        spacing={2}
                      >
                        <HStack>
                          <Text fontSize="sm" color="blue.600" fontWeight="bold">
                            #{model.rank}
                          </Text>
                          <Text fontSize="sm" color="gray.700" fontWeight="medium">
                            {model.model}
                          </Text>
                          <Spacer />
                          <Badge colorScheme="orange" fontSize="xs">
                            {model.days.toFixed(0)} days avg
                          </Badge>
                        </HStack>
                        
                        {model.sample_url && (
                          <HStack spacing={2}>
                            <Link
                              href={model.sample_url}
                              isExternal
                              color="blue.500"
                              fontSize="xs"
                              fontWeight="medium"
                              _hover={{ color: 'blue.600', textDecoration: 'underline' }}
                            >
                              <HStack spacing={1}>
                                <Text>Search on OLX.ba</Text>
                                <Icon as={FiExternalLink} />
                              </HStack>
                            </Link>
                            {model.sample_price && (
                              <Badge colorScheme="green" fontSize="xs">
                                {model.sample_price.toLocaleString()} KM
                              </Badge>
                            )}
                          </HStack>
                        )}
                      </VStack>
                    ))}
                  </VStack>
                ) : (
                  <Text fontSize="sm" color="gray.500" textAlign="center" py={4}>
                    No sufficient data for this price range
                  </Text>
                )}
              </AccordionPanel>
            </AccordionItem>
          ))}
        </Accordion>
      </Box>
    </Box>
  );
};

export default PriceBrackets;